"""Paper 3 gates G6-G12 (paper3_gates.py) on synthetic run directories (tests/paper3_fakes.py): every gate's pass and fail path.

Every test starts from a clean run that passes everything, changes exactly one thing and asserts which gate reacts and what it
costs (round failure for G6/G12, an eligibility flag for G7-G11). No GPU, no cloud; the numbers of docs/PAPER3_ENERGY_DESIGN.md
section 4 are restated here, not read from the module.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_gates as pg  # noqa: E402
from paper3_fakes import (HOST_ENTRIES, MONO0 as MONO_ORIGIN, T0, RunBuilder, edit_json, edit_results, edit_schedule_item, host_gates, mono,  # noqa: E402
                          utc)


def clean_captures(**overrides):
    base = [{"format": 1, "label": label, "gpu_compute_apps": {"returncode": 0, "apps": []},
             "scheduled": {"systemd_timers": ["fstrim.timer"], "cron_files": [], "crontab_entries": [], "at_jobs": []}} for label in ("start", "end")]
    for capture in base:
        capture.update(overrides)
    return base


@pytest.fixture
def run(tmp_path):
    """A clean H100 run: a CPU engine (pulses) and a GPU engine (window onsets) at SF1."""
    return RunBuilder(tmp_path / "run", host="h100", engines=("polars", "polars_gpu")).build()


def session_gates(root, host, key, suite="baseline"):
    report = pg.evaluate_run(root, suite, host)
    return next(s for s in report["sessions"] if s["key"] == key)["gates"]


def one(root, host, gate, key):
    return session_gates(root, host, key)[gate]


POLARS, GPU = "baseline/sf1/polars", "baseline/sf1/polars_gpu"


# ---- host config -------------------------------------------------------------------------------------------------------------

def test_the_shipped_config_has_nothing_pending_and_a_null_allow_list_is_the_one_value_that_would_be(tmp_path):
    config = REPO / "paper3_hosts.json"
    unset = {h: pg.load_host_config(config, h).unset() for h in ("h100", "a100", "gh200", "b200", "c-gcp", "c-vm")}
    assert unset == {"h100": [], "a100": [], "gh200": [], "b200": [], "c-gcp": [], "c-vm": []}, "the C-GCP allow-list was filled by amendment A6 (2026-11-23)"
    document = json.loads(config.read_text())
    assert isinstance(document["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"], list)
    document["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"] = None                    # the state at the lock: the one value that was pending
    pending = tmp_path / "hosts-pending.json"
    pending.write_text(json.dumps(document))
    assert pg.load_host_config(pending, "c-gcp").unset() == ["allowed_scheduled"]
    assert all(pg.load_host_config(pending, h).unset() == [] for h in ("h100", "a100", "gh200", "b200", "c-vm"))
    assert pg.load_host_config(config, "h100").t_min_s == 1.0 and pg.load_host_config(config, "a100").t_min_s == 2.0
    assert pg.load_host_config(config, "gh200").t_min_s == 1.0, "GH200 P6 rerun against module.power.draw.instant: max(1 s, measured 1.0 s)"
    gh200 = pg.load_host_config(config, "gh200")
    assert (gh200.gpu_source, gh200.gpu_scope, gh200.cpu_source, gh200.cpu_series) == (
        "nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption", "module", "none", None), "CPU energy only inside the S1-module counter"
    assert "lambda-gh200-20261115T213713Z-sources.json" in json.loads(config.read_text())["hosts"]["gh200"]["cpu_energy_basis"]
    b200 = pg.load_host_config(config, "b200")
    assert b200.t_min_s == 1.0, "B200 P6 rerun against power.draw.instant (amendment A2): max(1 s, measured 1.0 s)"
    assert (b200.gpu_source, b200.gpu_scope, b200.cpu_source, b200.cpu_series) == (
        "nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption", "board", "none", None), "board scope (S1-counter), no CPU-energy source"
    entry = json.loads(config.read_text())["hosts"]["b200"]
    assert "lambda-b200-20261122T210145Z-p6.json" in entry["t_min_basis"] and "lambda-b200-20261116T052750Z-sources.json" in entry["cpu_energy_basis"]
    assert pg.load_host_config(config, "c-gcp").t_min_s == 2.0, "P6-CPU on the metal host: windows-mode t_min 2.0 s"
    assert "gcp-c3-highmem-192-metal-20261116T000855Z-p6-cpu.json" in json.loads(config.read_text())["hosts"]["c-gcp"]["t_min_basis"]
    assert pg.load_host_config(config, "gh200").gpu_scope == "module" and pg.load_host_config(config, "h100").gpu_scope == "board"
    assert pg.load_host_config(config, "c-vm").t_min_s == "not_applicable" and pg.load_host_config(config, "c-gcp").cpu_series == 4


def test_a_host_with_an_unset_value_is_refused_not_evaluated(tmp_path):
    root = RunBuilder(tmp_path / "run", host="gh200", engines=("polars",)).build().root
    for name, change in (("t_min_s", {"t_min_s": None}), ("gpu_source", {"source": None}), ("cpu_source", {"cpu_source": None}),
                         ("allowed_scheduled", None)):
        if change is None:
            host = host_gates("c-gcp", allowed_scheduled=None)
        else:
            host = host_gates("gh200", **change)
        with pytest.raises(pg.GateConfigError, match=name):
            pg.evaluate_run(root, "baseline", host)
    with pytest.raises(pg.GateConfigError, match="refuse to evaluate"):
        pg.evaluate_round({"baseline": root}, host_gates("gh200", t_min_s=None), [])


@pytest.mark.parametrize("change,message", [
    ({"extra": 1}, "unknown keys"), ({"kind": "tpu"}, "kind"), ({"t_min_s": "soon"}, "t_min_s"), ({"t_min_s": 0}, "t_min_s"),
    ({"t_min_s": True}, "t_min_s"), ({"t_min_s": "not_applicable"}, "only for a host that measures no energy"),
    ({"energy_measured": "yes"}, "energy_measured"), ({"gpu_counter": {"source": "x"}}, "gpu_counter"),
    ({"gpu_counter": {"source": "x", "scope": "planet"}}, "scope"), ({"cpu_energy": {"source": "x", "series": 0}}, "series"),
    ({"exclusive_use": {"allowed_scheduled": "timer:x"}}, "allowed_scheduled"), ({"gpu_counter": {"source": 3, "scope": "board"}}, "source"),
    ({"cpu_energy_basis": ""}, "cpu_energy_basis must be a non-empty string"), ({"t_min_basis": 3}, "t_min_basis must be a non-empty string"),
])
def test_a_malformed_host_entry_is_a_config_error(change, message):
    entry = copy.deepcopy(HOST_ENTRIES["h100"])
    entry.update(change)
    with pytest.raises(pg.GateConfigError, match=message):
        pg.parse_host_entry("h100", entry)
    entry = copy.deepcopy(HOST_ENTRIES["h100"])
    del entry["t_min_s"]
    with pytest.raises(pg.GateConfigError, match="t_min_s is absent"):
        pg.parse_host_entry("h100", entry)


def test_a_bad_config_file_is_a_config_error(tmp_path):
    path = tmp_path / "hosts.json"
    for text in ("not json", json.dumps({"format": 2, "hosts": {}}), json.dumps({"format": 1, "hosts": {"a100": HOST_ENTRIES["a100"]}})):
        path.write_text(text)
        with pytest.raises(pg.GateConfigError):
            pg.load_host_config(path, "h100")
    with pytest.raises(pg.GateConfigError):
        pg.load_host_config(tmp_path / "missing.json", "h100")


def test_the_host_entry_digest_covers_that_host_only(tmp_path):
    path = tmp_path / "hosts.json"
    document = {"format": 1, "hosts": copy.deepcopy({"h100": HOST_ENTRIES["h100"], "a100": HOST_ENTRIES["a100"]})}
    path.write_text(json.dumps(document))
    before = pg.host_entry_digest(path, "h100")
    document["hosts"]["a100"]["t_min_s"] = 9.0
    path.write_text(json.dumps(document))
    assert pg.host_entry_digest(path, "h100") == before and pg.host_entry_digest(path, "a100") != before
    document["hosts"]["h100"]["t_min_s"] = 1.5
    path.write_text(json.dumps(document))
    assert pg.host_entry_digest(path, "h100") != before


# ---- a clean run ---------------------------------------------------------------------------------------------------------------

def test_a_clean_run_passes_every_gate(run):
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False and report["round_failures"] == [] and report["ineligible"] == {}
    for session in report["runs"][0]["sessions"]:
        assert {g: r["outcome"] for g, r in session["gates"].items()} == {g: "pass" for g in ("G6", "G7", "G8", "G10", "G11", "G12")}, session["key"]
        assert all(session["flags"].values())
    assert all(c["outcome"] == "pass" for c in report["runs"][0]["cells"]) and len(report["runs"][0]["cells"]) == 4
    assert report["gates"]["G12"] == {"pass": 3, "fail": 0, "not_evaluable": 0, "not_applicable": 0, "consequence": pg.CONSEQUENCE["G12"]}
    assert report["gates"]["G9"]["pass"] == 4


def test_the_report_is_a_pure_function_of_the_files(run):
    first = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    second = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert "evaluated_at" not in json.dumps(first)


def test_the_report_holds_no_energy_power_or_cost_magnitude(run):
    """Section 4 / 7.3: before the campaign ends only gate outcomes, counts, coverage, lags and ratios may be looked at."""
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    forbidden = ("power_w", "mean_power", "delta_mj", "energy_j", "joule", "watt", "_mj", "usd", "cost")
    keys = set()

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                keys.add(k)
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
    walk({k: v for k, v in report.items() if k != "interpretations"})
    assert not [k for k in keys if any(f in k.lower() for f in forbidden)], sorted(keys)


def test_a_session_is_judged_alone_never_against_another_engine(tmp_path):
    """Metamorphic: the gate results of a session do not depend on which other engines ran in the same run."""
    both = RunBuilder(tmp_path / "both", host="h100", engines=("polars", "polars_gpu")).build()
    only_cpu = RunBuilder(tmp_path / "cpu", host="h100", engines=("polars",)).build()
    host = host_gates("h100")
    assert session_gates(both.root, host, POLARS) == session_gates(only_cpu.root, host, POLARS)
    swapped = RunBuilder(tmp_path / "swap", host="h100", engines=("polars_gpu", "polars")).build()
    alone = RunBuilder(tmp_path / "gpu", host="h100", engines=("polars_gpu",)).build()
    assert session_gates(swapped.root, host, GPU) == session_gates(alone.root, host, GPU)


@pytest.mark.parametrize("host,per_run,engines", [
    ("a100", {("polars", "q01"): 2.5, ("polars", "q03"): 2.5, ("polars_gpu", "q01"): 2.2, ("polars_gpu", "q03"): 2.2}, ("polars", "polars_gpu")),
    ("gh200", {("spark_gpu", "q01"): 1.8, ("spark_gpu", "q03"): 1.8}, ("spark_cpu", "spark_gpu")),
    ("c-gcp", {}, ("polars", "spark_cpu")),
])
def test_a_clean_run_passes_on_every_host_class(tmp_path, host, per_run, engines):
    run = RunBuilder(tmp_path / "run", host=host, engines=engines, per_run=per_run).build()
    report = pg.evaluate_round({"baseline": run.root}, host_gates(host), clean_captures(**({"gpu_compute_apps": None} if host == "c-gcp" else {})))
    assert report["round_failed"] is False and report["ineligible"] == {}, report["round_failures"]
    assert {r["outcome"] for s in report["runs"][0]["sessions"] for r in s["gates"].values()} <= {"pass", "not_applicable"}
    assert report["gates"]["G6"]["pass"] == 2 and all(c["outcome"] == "pass" for c in report["runs"][0]["cells"])


# ---- G6 -------------------------------------------------------------------------------------------------------------------------

def g6(root, host=None, key=GPU):
    return one(root, host or host_gates("h100"), "G6", key)


def test_g6_fails_on_a_counter_that_is_not_the_probe_declared_one(run):
    edit_results(run.root, "1", "polars_gpu", lambda r: r[pg.COUNTER_BLOCK].update(source="pynvml.nvmlDeviceGetTotalEnergyConsumption"))
    result = g6(run.root)
    assert result["outcome"] == "fail" and "probe-declared" in result["problems"][0]
    edit_results(run.root, "1", "polars_gpu", lambda r: r[pg.COUNTER_BLOCK].update(source="nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption", scope="module"))
    assert any("scope" in p for p in g6(run.root)["problems"])
    edit_results(run.root, "1", "polars_gpu", lambda r: r[pg.COUNTER_BLOCK].update(scope="board", scope_tag="S1-module"))
    assert any("scope tag" in p for p in g6(run.root)["problems"])


def test_g6_fails_the_round_and_marks_the_session_energy_invalid(run):
    edit_results(run.root, "1", "polars_gpu", lambda r: r[pg.COUNTER_BLOCK].update(source="x"))
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is True and report["round_failures"][0].startswith("G6 baseline/sf1/polars_gpu:")
    assert report["ineligible"] == {GPU: ["energy_valid"]}
    assert report["gates"]["G6"]["fail"] == 1 and "round fails" in report["gates"]["G6"]["consequence"]


def test_g6_fails_on_a_missing_or_errored_counter_read_in_a_measured_run(run):
    def drop_delta(results):
        record = next(r for r in results["records"] if r["phase"] == "measured")
        record["energy_window"]["runs"][2]["gpu_energy"].update(delta_mj=None, error="NVML_ERROR_UNKNOWN")
    edit_results(run.root, "1", "polars_gpu", drop_delta)
    assert any("missing, errored or negative" in p for p in g6(run.root)["problems"])


def test_g6_fails_on_a_counter_that_goes_backwards_mid_session(run):
    def backwards(results):
        record = [r for r in results["records"] if r["phase"] == "measured"][2]
        run_ = record["energy_window"]["runs"][3]["gpu_energy"]
        run_["start_mj"] = 10
        run_["end_mj"] = 20
    edit_results(run.root, "1", "polars_gpu", backwards)
    assert any("not monotone" in p for p in g6(run.root)["problems"])


def test_g6_checks_the_stamps_and_the_counter_readings_at_launch_and_exit(run):
    cases = [
        (lambda i: i.pop("launch"), "launch stamp missing"), (lambda i: i.pop("process_exit"), "process_exit stamp missing"),
        (lambda i: i["launch"].update(monotonic_ns="x"), "launch stamp missing or malformed"),
        (lambda i: i["process_exit"].update(utc="2026-11-17T06:00:00"), "process_exit stamp missing or malformed"),
    ]
    for change, message in cases:
        fresh = copy.deepcopy(run.schedule)
        edit_schedule_item(run.root, "1", "polars_gpu", change)
        assert any(message in p for p in g6(run.root)["problems"]), message
        (run.root / "schedule.json").write_text(json.dumps(fresh))
    assert g6(run.root)["outcome"] == "pass"


@pytest.mark.parametrize("change,message", [
    (lambda r: r["session_counters"].pop("completed"), "session_counters.completed stamp missing"),
    (lambda r: r["session_counters"]["engine_ready"].pop("gpu_energy_read"), "holds no usable GPU counter reading"),
    (lambda r: r["session_counters"]["completed"]["gpu_energy_read"].update(mj=None, error="x"), "holds no usable GPU counter reading"),
    (lambda r: r["session_counters"]["engine_ready"]["gpu_energy_read"].update(source="nvidia-smi:total_energy_consumption"), "probe-declared"),
    (lambda r: r["session_counters"]["engine_ready"]["gpu_energy_read"].update(scope_tag="none"), "probe-declared"),
    (lambda r: r.pop("completed_at_utc"), "completed_at_utc missing"),
    (lambda r: r["session_counters"]["completed"].update(monotonic_ns=1), "out of order"),
])
def test_g6_checks_the_workers_session_counters(run, change, message):
    edit_results(run.root, "1", "polars_gpu", change)
    assert any(message in p for p in g6(run.root)["problems"]), g6(run.root)


def test_g6_is_not_applicable_to_a_session_that_did_not_complete_and_fails_a_complete_one_without_results(run):
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i.update(status="failed", returncode=1))
    assert g6(run.root)["outcome"] == "not_applicable"
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i.update(status="complete", returncode=0))
    (run.root / "sf1" / "polars_gpu" / "results.json").unlink()
    assert g6(run.root) == {"outcome": "fail", "problems": ["results.json missing or unreadable"]}


def test_g6_on_the_cpu_only_host_checks_the_rapl_series(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars", "spark_cpu")).build()
    host = host_gates("c-gcp")
    key = "baseline/sf1/polars"
    assert one(run.root, host, "G6", key)["outcome"] == "pass"

    def sidecar(change):
        edit_json(run.root / "power_sources.json", change)

    def restore():
        run.write_rapl()
    sidecar(lambda s: s["liveness"]["powercap-rapl/intel-rapl:1"].update(verdict="stalled"))
    informed = one(run.root, host, "G6", key)
    assert informed["outcome"] == "pass", "the sampler's invocation-wide verdict is information; the session's own samples decide"
    assert informed["invocation_liveness"]["powercap-rapl/intel-rapl:1"] == "stalled"
    restore()
    sidecar(lambda s: s.update(cpu_energy_source="perf-stat"))
    assert any("probe-declared 'powercap-rapl'" in p for p in one(run.root, host, "G6", key)["problems"])
    restore()
    sidecar(lambda s: s["sources"][0]["domains"].pop())
    assert any("1 sampled series" not in p and "3 sampled series, probe-declared 4" in p for p in one(run.root, host, "G6", key)["problems"])
    restore()
    assert one(run.root, host_gates("c-gcp", cpu_series=2), "G6", key)["problems"][0].startswith("4 sampled series, probe-declared 2")
    lines = (run.root / "power.csv").read_text().splitlines()
    span = pg.epoch(json.loads((run.root / "sf1" / "polars" / "results.json").read_text())["started_at_utc"]) + 20.0
    target = next(i for i, l in enumerate(lines[1:], 1) if pg.epoch(l.split(",")[0]) > span and "intel-rapl:1:0" in l)
    columns = lines[target].split(",")
    columns[8] = "decrease"                                                  # the flag column of power.csv
    lines[target] = ",".join(columns)
    (run.root / "power.csv").write_text("\n".join(lines) + "\n")
    flagged = one(run.root, host, "G6", key)
    assert flagged["outcome"] == "fail" and any(
        "series intel-rapl:1:0: session energy not computable: a sample the window uses is flagged decrease" in p for p in flagged["problems"])
    assert one(run.root, host, "G6", "baseline/sf1/spark_cpu")["outcome"] == "pass", "a flag in another session's span is not this session's"
    restore()
    lines = [l for l in (run.root / "power.csv").read_text().splitlines() if "intel-rapl:1:0" not in l]
    (run.root / "power.csv").write_text("\n".join(lines) + "\n")
    assert any("3 counter series, probe-declared 4" in p for p in one(run.root, host, "G6", key)["problems"])
    restore()
    assert one(run.root, host, "G6", key)["outcome"] == "pass"


def rapl_rows(run, zone, start, end, change):
    """Rewrite the power.csv rows of one zone between two epoch times: change(row_columns) returns the new columns, or None to drop."""
    lines = (run.root / "power.csv").read_text().splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        columns = line.split(",")
        if columns[3] == zone and start <= pg.epoch(columns[0]) <= end:
            columns = change(columns)
            if columns is None:
                continue
        out.append(",".join(columns))
    (run.root / "power.csv").write_text("\n".join(out) + "\n")


def session_start(run, engine):
    return pg.epoch(json.loads((run.root / "sf1" / engine / "results.json").read_text())["started_at_utc"])


def test_g6_judges_the_liveness_of_a_series_inside_the_session_from_its_own_samples(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars", "spark_cpu")).build()
    host, key, other = host_gates("c-gcp"), "baseline/sf1/polars", "baseline/sf1/spark_cpu"
    start = session_start(run, "polars")
    frozen = {}

    def freeze(columns):
        frozen.setdefault("value", columns[5])
        columns[5] = columns[7] = frozen["value"]
        return columns
    rapl_rows(run, "intel-rapl:1:0", start + 20.0, start + 25.0, freeze)             # flat for 5 s: the limit, not beyond
    assert one(run.root, host, "G6", key)["outcome"] == "pass"
    frozen.clear()
    rapl_rows(run, "intel-rapl:1:0", start + 20.0, start + 27.0, freeze)             # flat for 7 s
    result = one(run.root, host, "G6", key)
    assert result["outcome"] == "fail" and any("series intel-rapl:1:0: the counter was flat for" in p for p in result["problems"])
    assert one(run.root, host, "G6", other)["outcome"] == "pass", "another session's samples are not this session's"
    run.write_rapl()
    rapl_rows(run, "intel-rapl:1", start + 30.0, start + 62.0, lambda columns: None)  # 32 s without a sample
    result = one(run.root, host, "G6", key)
    assert result["outcome"] == "fail" and any("between two samples inside the session (limit 30 s)" in p for p in result["problems"])
    run.write_rapl()
    rapl_rows(run, "intel-rapl:1", start + 30.0, start + 58.0, lambda columns: None)  # 28 s: inside the limit
    assert one(run.root, host, "G6", key)["outcome"] == "pass"
    run.write_rapl()
    first = {}

    def hold(columns):
        first.setdefault("value", columns[5])
        columns[5] = columns[7] = first["value"]
        return columns
    rapl_rows(run, "intel-rapl:0:0", start - 1.0, start + 1000.0, hold)               # never advances
    result = one(run.root, host, "G6", key)
    assert result["outcome"] == "fail" and any("did not advance" in p for p in result["problems"])


def test_the_gates_use_the_shared_power_csv_reader_and_interpolation(tmp_path):
    """One implementation for the P6-CPU probe, the gates and the analysis (power_telemetry.py): the gates add nothing of their own."""
    import power_telemetry as pt
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",), post_idle_scale=1.3).build()
    assert pg.read_power_series(run.root / "power.csv") == pt.read_power_csv_series(run.root / "power.csv")
    assert pg.read_power_series(run.root / "missing.csv") is None
    series = pt.read_power_csv_series(run.root / "power.csv")
    report = json.loads((run.root / "power_sources.json").read_text())
    results = json.loads((run.root / "sf1" / "polars" / "results.json").read_text())
    shared = {}
    for scope, zones in (("S2-all", pt.all_package_dram_zones(report)), ("S2-pinned", pt.pinned_energy_zones(report))):
        watts = []
        for window in results["idle_windows"]:
            start, end = window["start_monotonic_ns"], window["end_monotonic_ns"]
            watts.append(pt.s2_pinned_energy_j(series, zones, start, end)["joules"] / ((end - start) / 1e9))
        shared[scope] = round(abs(watts[1] - watts[0]) / ((watts[0] + watts[1]) / 2), 5)
    assert one(run.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")["relative_difference"] == shared
    assert pt.all_package_dram_zones(report) == ["intel-rapl:0", "intel-rapl:0:0", "intel-rapl:1", "intel-rapl:1:0"]
    assert pt.pinned_energy_zones(report) == ["intel-rapl:0", "intel-rapl:0:0"]


def test_core_sub_zones_are_not_added_to_the_cpu_scopes(tmp_path):
    steady = RunBuilder(tmp_path / "a", host="c-gcp", engines=("polars",), post_idle_scale=1.3).build()
    with_core = RunBuilder(tmp_path / "b", host="c-gcp", engines=("polars",), post_idle_scale=1.3).build()
    lines = (with_core.root / "power.csv").read_text().splitlines()
    extra = []
    for line in lines[1:]:
        columns = line.split(",")
        if columns[3] == "intel-rapl:0":                                   # a core sub-zone of package-0 with a steep, unrelated slope
            index = int(columns[1]) - int(MONO_ORIGIN)
            columns[3], columns[4], columns[5], columns[7] = "intel-rapl:0:1", "package-0/core", str(index * 50), str(index * 50)
            extra.append(",".join(columns))
    (with_core.root / "power.csv").write_text("\n".join([*lines, *extra]) + "\n")
    baseline = one(steady.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")["relative_difference"]
    assert one(with_core.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")["relative_difference"] == baseline
    assert any("5 counter series, probe-declared 4" in p for p in one(with_core.root, host_gates("c-gcp"), "G6", "baseline/sf1/polars")["problems"]), \
        "an unexpected series is still a source mismatch for G6"


def test_a_source_that_is_not_per_socket_has_one_summed_s2_all_and_no_pinned_scope(tmp_path):
    """perf-stat and turbostat sum all sockets in one series: the shared helper lists no zones, S2-all is every series, S2-pinned does not exist."""
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",), post_idle_scale=1.05).build()

    def not_per_socket(sidecar):
        sidecar["sockets"]["per_socket_counters"] = False
        sidecar["pinning"] = {"sockets": [0], "attribution": {"possible": False}}
    edit_json(run.root / "power_sources.json", not_per_socket)
    result = one(run.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")
    assert result["outcome"] == "pass" and set(result["relative_difference"]) == {"S2-all"}


def test_g10_has_no_idle_power_for_a_scope_whose_window_touches_a_flagged_sample(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",)).build()
    window = json.loads((run.root / "sf1" / "polars" / "results.json").read_text())["idle_windows"][1]
    middle = pg.epoch(window["start_utc"]) + 10.0
    rapl_rows(run, "intel-rapl:0", middle, middle + 0.5, lambda columns: columns[:8] + ["decrease"] + columns[9:])
    result = one(run.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")
    assert result["outcome"] == "fail" and set(result["unreliable_scopes"]) == {"S2-all", "S2-pinned"}
    assert any("no idle mean power for scope(s)" in p for p in result["problems"])


def test_g10_does_not_turn_a_negative_window_energy_into_an_idle_power(tmp_path):
    """energy_between_uj returns a negative number for a counter that stepped back without a flag; the gate treats it as no energy."""
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",)).build()
    window = json.loads((run.root / "sf1" / "polars" / "results.json").read_text())["idle_windows"][1]
    end = pg.epoch(window["end_utc"])
    rapl_rows(run, "intel-rapl:0", end - 0.5, end + 0.5, lambda columns: columns[:5] + ["1000"] + columns[6:7] + ["1000"] + columns[8:])
    result = one(run.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")
    assert result["outcome"] == "fail" and "S2-pinned" in result["unreliable_scopes"] and "S2-all" in result["unreliable_scopes"]
    assert any("no idle mean power for scope(s)" in p for p in result["problems"]), "missing, not a wild percentage"


def test_g6_catches_a_dip_in_the_counter_that_carries_no_flag(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars", "spark_cpu")).build()
    start = session_start(run, "polars")
    rapl_rows(run, "intel-rapl:1", start + 40.0, start + 40.5, lambda columns: columns[:5] + ["1000"] + columns[6:7] + ["1000"] + columns[8:])
    problems = one(run.root, host_gates("c-gcp"), "G6", "baseline/sf1/polars")["problems"]
    assert any("series intel-rapl:1: counter not monotone inside the session" in p for p in problems)


def test_g6_needs_a_power_csv_the_shared_reader_understands_and_the_sessions_stamps(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",)).build()
    (run.root / "power.csv").write_text("when,what\n1,2\n")
    problems = one(run.root, host_gates("c-gcp"), "G6", "baseline/sf1/polars")["problems"]
    assert any("power.csv holds 0 counter series, probe-declared 4" in p for p in problems)
    run.write_rapl()
    edit_schedule_item(run.root, "1", "polars", lambda i: i.pop("launch"))
    problems = one(run.root, host_gates("c-gcp"), "G6", "baseline/sf1/polars")["problems"]
    assert any("no launch and process-exit stamps to cut the CPU series by" in p for p in problems)
    (run.root / "power.csv").unlink()
    assert any("power.csv missing" in p for p in one(run.root, host_gates("c-gcp"), "G6", "baseline/sf1/polars")["problems"])


def test_g6_on_the_cpu_only_host_needs_samples_around_the_launch_and_exit_stamps(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",)).build()
    lines = (run.root / "power.csv").read_text().splitlines()
    keep = [lines[0]] + [l for l in lines[1:] if pg.epoch(l.split(",")[0]) < T0 + 5.0]      # the sampler stopped right after the launch
    (run.root / "power.csv").write_text("\n".join(keep) + "\n")
    problems = one(run.root, host_gates("c-gcp"), "G6", "baseline/sf1/polars")["problems"]
    assert any("session energy not computable: window outside the sampled range" in p for p in problems)


def test_a_host_that_declares_no_cpu_source_fails_g6_when_the_sampler_found_one(run):
    (run.root / "power_sources.json").write_text(json.dumps({"cpu_energy_source": "powercap-rapl"}))
    assert any("declares no CPU energy source" in p for p in g6(run.root)["problems"])


# ---- G7 -------------------------------------------------------------------------------------------------------------------------

def first_window(root, engine="polars_gpu"):
    results = json.loads((root / "sf1" / engine / "results.json").read_text())
    span = next(r for r in results["records"] if r["phase"] == "measured")["energy_window"]["span"]
    return pg.epoch(span["start_utc"]), pg.epoch(span["end_utc"])


def test_g7_fails_on_too_few_samples_but_leaves_everything_else_alone(run):
    run.write_gpu_csv(rows=lambda t: int((t - T0) / 0.2) % 10 not in (0, 1, 2))          # 70% of the samples
    result = one(run.root, host_gates("h100"), "G7", GPU)
    assert result["outcome"] == "fail" and result["flagged_windows"] and all(w["coverage"] < 0.8 for w in result["flagged_windows"])
    gates = session_gates(run.root, host_gates("h100"), GPU)
    assert gates["G6"]["outcome"] == "pass" and gates["G10"]["outcome"] == "pass", "the counter value is unaffected"
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False and "crosscheck_ok" in report["ineligible"][GPU]


def test_g7_fails_on_a_gap_longer_than_one_second_even_with_enough_samples(run):
    start, end = first_window(run.root)
    middle = (start + end) / 2
    run.write_gpu_csv(rows=lambda t: not (middle <= t <= middle + 1.3))
    result = one(run.root, host_gates("h100"), "G7", GPU)
    assert result["outcome"] == "fail" and any(w["max_gap_s"] > 1.0 and w["coverage"] > 0.8 for w in result["flagged_windows"])


def test_g7_passes_a_gap_of_exactly_the_limit_and_ignores_windows_below_10_seconds(run):
    start, end = first_window(run.root)
    run.write_gpu_csv(rows=lambda t: not (start + 3.0 <= t <= start + 3.0 + 0.7))        # 0.9 s spacing at most
    assert one(run.root, host_gates("h100"), "G7", GPU)["outcome"] == "pass"
    short = RunBuilder(run.root.parent / "short", host="h100", engines=("polars_gpu",), window_target=4.0).build()
    short.write_gpu_csv(rows=lambda t: False)
    result = one(short.root, host_gates("h100"), "G7", GPU)
    assert result["outcome"] == "fail", "the 30 s idle windows are still trapezoid windows"
    assert {w["label"] for w in result["flagged_windows"]} == {"before_warmups", "after_last_trial"}
    assert {w["kind"] for w in result["flagged_windows"]} == {"idle"}


def test_g7_fails_when_gpu_csv_is_not_stamped_in_utc(run):
    edit_json(run.root / "environment.json", lambda e: e["gpu_telemetry"].pop("monitor_tz_env"))
    result = one(run.root, host_gates("h100"), "G7", GPU)
    assert result["outcome"] == "fail" and "not stamped in UTC" in result["problems"][0]


def test_g7_is_not_applicable_without_a_gpu_stream(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",)).build()
    assert one(run.root, host_gates("c-gcp"), "G7", "baseline/sf1/polars")["outcome"] == "not_applicable"


# ---- G8 -------------------------------------------------------------------------------------------------------------------------

def test_g8_uses_window_onsets_for_a_gpu_engine_and_pulses_for_a_cpu_engine(run):
    gpu, cpu = one(run.root, host_gates("h100"), "G8", GPU), one(run.root, host_gates("h100"), "G8", POLARS)
    assert gpu["outcome"] == cpu["outcome"] == "pass"
    assert gpu["method"] == "windows" and gpu["usable_onsets"] == 6 and 0.15 <= gpu["median_lag_s"] < 0.4 and abs(gpu["drift_s"]) <= 0.2
    assert cpu["method"] == "pulses" and cpu["pulses"] == 2 and cpu["usable_onsets"] == 2 and cpu["windows_considered"] == 0


def test_g8_fails_on_a_median_onset_lag_beyond_0_4_seconds(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars", "polars_gpu"), skew=0.6).build()
    for key in (GPU, POLARS):
        result = one(run.root, host_gates("h100"), "G8", key)
        assert result["outcome"] == "fail" and any("median onset lag" in p for p in result["problems"]), key
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False, "G8 only disables gpu.csv values"
    assert set(report["ineligible"]) == {GPU, POLARS} and report["ineligible"][GPU] == ["gpu_csv_enabled"]


def test_g8_passes_a_median_just_inside_the_limit_and_fails_just_outside(tmp_path):
    inside = RunBuilder(tmp_path / "in", host="h100", engines=("polars_gpu",), skew=0.20, grid_phase=0.0).build()
    assert one(inside.root, host_gates("h100"), "G8", GPU)["outcome"] == "pass"
    outside = RunBuilder(tmp_path / "out", host="h100", engines=("polars_gpu",), skew=0.41, grid_phase=0.0).build()
    result = one(outside.root, host_gates("h100"), "G8", GPU)
    assert result["outcome"] == "fail" and 0.4 < result["median_lag_s"]


def test_g8_reads_a_clock_that_runs_behind_as_a_negative_lag(tmp_path):
    mild = RunBuilder(tmp_path / "mild", host="h100", engines=("polars_gpu", "polars"), skew=-0.30, grid_phase=0.0).build()
    result = one(mild.root, host_gates("h100"), "G8", GPU)
    assert result["outcome"] == "pass" and -0.4 < result["median_lag_s"] < 0, "within +-0.4 s on the early side too"
    far = RunBuilder(tmp_path / "far", host="h100", engines=("polars_gpu", "polars"), skew=-0.65, grid_phase=0.0).build()
    result = one(far.root, host_gates("h100"), "G8", GPU)
    assert result["outcome"] == "fail" and result["median_lag_s"] < -0.4
    assert one(far.root, host_gates("h100"), "G8", POLARS)["outcome"] == "fail", "the pulses see it too"


def test_g8_a_stream_stamped_ahead_fails_through_the_pulses_even_when_every_window_is_busy_before(tmp_path):
    """Windows follow earlier activity within the second before their start, so an early stream makes every window busy_before; the pulses
    (the runner waits for a quiet GPU before each) then see the offset as a negative lag and fail the session instead of skipping it."""
    for skew in (-0.65, -0.9):
        early = RunBuilder(tmp_path / f"early{skew}", host="h100", engines=("polars_gpu", "polars"), skew=skew, grid_phase=0.0).build()
        gpu = one(early.root, host_gates("h100"), "G8", GPU)
        assert gpu["outcome"] == "fail" and gpu["method"] == "pulses" and gpu["windows_skipped"]["busy_before"] == 6, skew
        assert gpu["median_lag_s"] < -0.4 and any("median onset lag" in p for p in gpu["problems"]), skew
        assert one(early.root, host_gates("h100"), "G8", POLARS)["outcome"] == "fail", skew
    report = pg.evaluate_round({"baseline": early.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False and report["gates"]["G8"]["fail"] == 2 and set(report["ineligible"]) == {GPU, POLARS}


def test_g8_an_offset_beyond_the_quiet_range_is_not_scored_and_the_session_is_not_evaluable(tmp_path):
    """A stream ahead by more than the second the pulse search reaches back puts the onset into the quiet range: busy_before, no lag."""
    far = RunBuilder(tmp_path / "far", host="h100", engines=("polars_gpu", "polars"), skew=-1.6, grid_phase=0.0).build()
    for key in (GPU, POLARS):
        result = one(far.root, host_gates("h100"), "G8", key)
        assert result["outcome"] == "not_evaluable" and result["pulses_skipped"]["busy_before"] == 2, key


def test_g8_fails_on_a_drift_of_the_lag_over_the_session(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars_gpu",), skew=lambda t: 0.05 + 0.007 * (t - T0 - 50.0)).build()   # lag grows 0.1 to 0.55 s
    result = one(run.root, host_gates("h100"), "G8", GPU)
    assert result["outcome"] == "fail" and any("drift" in p for p in result["problems"]) and abs(result["drift_s"]) > 0.2
    assert abs(result["median_lag_s"]) < 0.4, "the median alone would have passed: it is the drift that fails"


def test_g8_is_not_evaluable_without_two_onsets_and_is_never_a_pass(run):
    edit_schedule_item(run.root, "1", "polars", lambda i: i.pop(pg.G8_PULSES_KEY))
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "not_evaluable" and result["method"] == "none" and "0 pulse record(s) with 0 onset(s)" in result["reasons"][0]
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False, "not evaluable disables gpu.csv values; it does not fail the round"
    assert report["gates"]["G8"]["not_evaluable"] == 1 and report["gates"]["G8"]["pass"] == 1 and report["gates"]["G8"]["fail"] == 0
    assert report["ineligible"] == {POLARS: ["gpu_csv_enabled"]}
    session = next(s for s in report["runs"][0]["sessions"] if s["key"] == POLARS)
    assert session["flags"]["gpu_csv_enabled"] is False


def test_g8_needs_both_pulses_and_counts_a_pulse_that_did_not_run_as_missing(run):
    edit_schedule_item(run.root, "1", "polars", lambda i: i[pg.G8_PULSES_KEY].pop("end"))
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "not_evaluable" and "1 pulse record(s) with 1 onset(s)" in result["reasons"][0] and result["pulses"] == 1
    edit_schedule_item(run.root, "1", "polars", lambda i: i[pg.G8_PULSES_KEY].update(
        end={"ok": False, "label": "end", "error": "OSError: libcuda.so.1: cannot open shared object file"}, start=dict(i[pg.G8_PULSES_KEY]["start"], ok=False)))
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "not_evaluable" and "2 pulse record(s) with 0 onset(s)" in result["reasons"][0]


def test_g8_ignores_the_pulses_of_a_gpu_engine_session_that_has_usable_window_onsets(run):
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i.update({pg.G8_PULSES_KEY: {"start": {"ok": False}, "end": {"ok": False}}}))
    result = one(run.root, host_gates("h100"), "G8", GPU)
    assert result["outcome"] == "pass" and result["method"] == "windows"


def test_g8_skips_windows_that_follow_earlier_activity_and_falls_back_to_the_pulses(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars_gpu",), window_gap=0.2).build()
    result = one(run.root, host_gates("h100"), "G8", GPU)
    assert result["windows_skipped"]["busy_before"] >= 5 and result["outcome"] == "pass" and result["method"] == "pulses" and result["pulses"] == 2
    bare = RunBuilder(tmp_path / "bare", host="h100", engines=("polars_gpu",), window_gap=0.2, extra_pulses=False).build()
    result = one(bare.root, host_gates("h100"), "G8", GPU)
    assert result["windows_skipped"]["busy_before"] >= 5 and result["outcome"] == "not_evaluable" and result["method"] == "none"


def start_pulse_launch(root, engine):
    item = next(i for i in json.loads((root / "schedule.json").read_text()) if i["engine"] == engine)
    return pg.epoch(item[pg.G8_PULSES_KEY]["start"]["launch_utc"])


def test_g8_a_start_pulse_right_after_the_previous_end_pulse_is_busy_before_and_not_scored(tmp_path):
    """The 2026-11-22 H100 smoke test: three start pulses were launched while the previous session's end pulse was still in the
    stream, because the runner did not wait for a quiet GPU. Such a pulse has no onset: no lag is scored, the session is not evaluable.
    (One session here: the builder's session gaps do not model the runner's wait.)"""
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars",)).build()
    clean = one(run.root, host_gates("h100"), "G8", POLARS)
    assert clean["outcome"] == "pass" and clean["method"] == "pulses" and clean["usable_onsets"] == 2, "a quiet GPU before the pulse: scored"
    assert clean["pulses_skipped"] == {"busy_before": 0, "quiet_not_shown": 0, "missed": 0, "no_utilization_column": 0}
    launch = start_pulse_launch(run.root, "polars")
    run.busy.append((launch - 2.5, launch - 0.4))                        # the previous end pulse's load reaches back into [launch - 2 s, launch - 1 s)
    run.write_gpu_csv()
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "not_evaluable" and result["method"] == "none" and result["pulses"] == 2
    assert result["pulses_skipped"]["busy_before"] == 1 and "usable_onsets" not in result and "median_lag_s" not in result
    assert "1 onset(s)" in result["reasons"][0] and "busy_before: 0 window(s), 1 pulse(s)" in result["reasons"][0]
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False and report["gates"]["G8"]["not_evaluable"] == 1 and report["ineligible"] == {POLARS: ["gpu_csv_enabled"]}


def test_g8_load_only_in_the_last_second_before_a_pulse_reads_as_a_clock_ahead_and_fails(tmp_path):
    """The runner's quiet wait keeps this from happening; without it, load that starts inside the second before the launch is
    indistinguishable from a stream stamped ahead of the pulse, and is scored as a negative lag (here: a drift of -0.9 s)."""
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars",)).build()
    launch = start_pulse_launch(run.root, "polars")
    run.busy.append((launch - 0.9, launch - 0.02))
    run.write_gpu_csv()
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "fail" and result["usable_onsets"] == 2 and result["pulses_skipped"]["busy_before"] == 0
    assert any("drift" in p for p in result["problems"])


def test_g8_a_pulse_launched_two_seconds_after_the_load_ended_is_scored_and_one_launched_1_3_seconds_after_is_not(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars",)).build()
    launch = start_pulse_launch(run.root, "polars")
    run.busy.append((launch - 5.0, launch - 2.3))                        # stamped 0.15 s later: the load is out of the stream 2.15 s before the launch
    run.write_gpu_csv()
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "pass" and result["usable_onsets"] == 2 and result["pulses_skipped"]["busy_before"] == 0
    run.busy.append((launch - 5.0, launch - 1.3))                        # 1.15 s: the load is still in [launch - 2 s, launch - 1 s)
    run.write_gpu_csv()
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "not_evaluable" and result["pulses_skipped"]["busy_before"] == 1


def test_g8_a_pulse_without_a_sample_in_its_quiet_range_is_not_scored(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars",)).build()
    launch = start_pulse_launch(run.root, "polars")
    run.write_gpu_csv(rows=lambda t: not (launch - 2.5 <= t < launch - 1.0))   # nothing shows the GPU quiet in [launch - 2 s, launch - 1 s)
    result = one(run.root, host_gates("h100"), "G8", POLARS)
    assert result["outcome"] == "not_evaluable" and result["pulses_skipped"]["quiet_not_shown"] == 1 and result["pulses_skipped"]["busy_before"] == 0
    assert "quiet_not_shown: 0 window(s), 1 pulse(s)" in result["reasons"][0]


def test_g8_a_window_that_follows_a_short_earlier_activity_is_busy_before_not_a_negative_lag(tmp_path):
    """The 2026-11-22 smoke test, polars_gpu: the first window followed a warm-up run by 0.14 s and was scored as a lag of -0.144 s,
    which made the drift fail (-0.462 s). A window's quiet range is the second before its start, so that window has no onset."""
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars_gpu",)).build()
    clean = one(run.root, host_gates("h100"), "G8", GPU)
    assert clean["outcome"] == "pass" and clean["method"] == "windows" and clean["usable_onsets"] == 6
    first = first_window(run.root)[0]
    run.busy.append((first - 0.5, first - 0.02))
    run.write_gpu_csv()
    result = one(run.root, host_gates("h100"), "G8", GPU)
    assert result["outcome"] == "pass" and result["method"] == "windows" and result["usable_onsets"] == 5
    assert result["windows_skipped"]["busy_before"] == 1 and abs(result["drift_s"]) <= 0.2


def test_g8_reports_the_quiet_ranges_beside_the_thresholds_and_leaves_them_unchanged(run):
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["thresholds"]["g8_median_lag_s"] == 0.4 and report["thresholds"]["g8_drift_s"] == 0.2
    assert report["thresholds"]["g8_utilization_percent"] == 10.0
    assert report["thresholds"]["g8_window_quiet"] == [1.0, 0.0, 0.0] and report["thresholds"]["g8_pulse_quiet"] == [2.0, 1.0, 1.0]


def test_g8_counts_a_window_without_any_gpu_activity_as_a_missed_onset(run):
    run.busy[:] = [b for b in run.busy if not (first_window(run.root)[0] - 0.1 <= b[0] <= first_window(run.root)[1])]
    run.write_gpu_csv()
    result = one(run.root, host_gates("h100"), "G8", GPU)
    assert result["windows_skipped"]["missed"] >= 1


def test_g8_requires_the_chrony_offset_to_be_logged_but_sets_no_threshold(run):
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i["clock_sync"]["end"].update(returncode=None, system_time_offset_seconds=None))
    result = one(run.root, host_gates("h100"), "G8", GPU)
    assert result["outcome"] == "fail" and result["problems"] == ["chronyc tracking offset not logged at session end"]
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i["clock_sync"]["end"].update(returncode=0, system_time_offset_seconds=-3.0))
    assert one(run.root, host_gates("h100"), "G8", GPU)["outcome"] == "pass", "an offset of 3 s is logged, not judged"


def test_onset_and_drift_helpers():
    W = pg.WINDOW_QUIET
    gpu = {"t": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2], "columns": {"utilization.gpu": [0, 0, 5, 10, 90, 90, 90]}}
    assert pg.onset_lag(gpu, 0.25, W) == (pytest.approx(0.35), "ok")
    assert pg.onset_lag(gpu, 1.1, W) == (None, "busy_before"), "a window: a sample >= 10% in the second before the start is earlier work"
    assert pg.onset_lag(gpu, 3.0, W)[1] == "quiet_not_shown" and pg.onset_lag(gpu, 30.0, W)[1] == "quiet_not_shown", "no sample shows it quiet"
    busy = {"t": [0.0, 0.2, 0.4, 0.6, 0.8], "columns": {"utilization.gpu": [95, 95, 0, 0, 95]}}
    assert pg.onset_lag(busy, 0.9, W) == (None, "busy_before") and pg.onset_lag(busy, 1.3, W) == (None, "busy_before")
    assert pg.onset_lag({"t": [0.0, 0.2], "columns": {"utilization.gpu": [None, None]}}, 0.0, W) == (None, "quiet_not_shown")
    assert pg.onset_lag({"t": [0.0], "columns": {}}, 0.0, W) == (None, "no_utilization_column")
    quiet = {"t": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6], "columns": {"utilization.gpu": [0, 0, 0, 0, 0, 0, 0, 90, 90]}}
    assert pg.onset_lag(quiet, 1.2, W) == (pytest.approx(0.2), "ok"), "a quiet second before the stamp, then the onset 0.2 s after it"
    assert pg.onset_lag(quiet, 3.5, W)[1] == "quiet_not_shown" and pg.onset_lag(quiet, 0.5, W) == (pytest.approx(0.9), "ok")
    assert pg.onset_lag({"t": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0], "columns": {"utilization.gpu": [0, 0, 0, 0, 0, 0]}}, 1.0, W) == (None, "missed"), \
        "quiet before but nothing within 3 s after"
    edge = {"t": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4], "columns": {"utilization.gpu": [90, 0, 0, 0, 0, 0, 90, 90]}}
    assert pg.onset_lag(edge, 1.0, W) == (None, "busy_before"), "the second is [start - 1 s, start): a sample at exactly start - 1 s is in it"
    assert pg.onset_lag(edge, 1.0 + 1e-9, W) == (pytest.approx(0.2), "ok"), "a hair later the 90% sample at 0.0 s is out of it"
    assert pg.onset_lag(edge, 1.2, W) == (pytest.approx(0.0), "ok"), "a sample at exactly the stamp is the onset search's, not the quiet range's"
    # a pulse (the default): quiet over [stamp - 2 s, stamp - 1 s), the onset searched from stamp - 1 s with a signed lag
    P = pg.PULSE_QUIET
    steady = {"t": [i * 0.2 for i in range(40)], "columns": {"utilization.gpu": [0] * 9 + [90] * 31}}      # quiet until 1.8 s, then busy
    assert pg.onset_lag(steady, 2.5) == (pytest.approx(-0.7), "ok"), "an onset 0.7 s before the stamp is a negative lag, scored"
    assert pg.onset_lag(steady, 2.0) == (pytest.approx(-0.2), "ok") and pg.onset_lag(steady, 1.8) == (pytest.approx(0.0), "ok")
    assert pg.onset_lag(steady, 3.0) == (None, "busy_before"), "load already in [stamp - 2 s, stamp - 1 s) = [1.0, 2.0) is earlier work"
    assert pg.onset_lag(steady, 10.0)[1] == "quiet_not_shown" and pg.onset_lag(steady, 0.5)[1] == "quiet_not_shown"
    gap = {"t": [0.0, 0.2, 0.4, 3.0, 3.2], "columns": {"utilization.gpu": [0, 0, 0, 0, 90]}}
    assert pg.onset_lag(gap, 3.0) == (None, "quiet_not_shown"), "no sample in [1.0, 2.0): nothing shows it quiet"
    assert pg.thirds_drift([0.2]) is None and pg.thirds_drift([0.2, 0.5]) == pytest.approx(-0.3)
    assert pg.thirds_drift([0.1, 0.1, 0.2, 0.2, 0.5, 0.5]) == pytest.approx(-0.4)
    assert pg.g8_verdict([0.3], 0)["outcome"] == "fail"
    assert pg.g8_verdict([0.39, 0.39, 0.39], 0)["outcome"] == "pass" and pg.g8_verdict([0.41, 0.41], 0)["outcome"] == "fail"
    assert pg.g8_verdict([-0.39, -0.39], 0)["outcome"] == "pass" and pg.g8_verdict([-0.41, -0.41], 0)["outcome"] == "fail"
    assert pg.g8_verdict([0.2, 0.2, 0.2, 0.2, 0.4, 0.4], 0)["outcome"] == "pass", "a drift of exactly 0.2 s is within the limit"
    assert pg.g8_verdict([0.2, 0.2, 0.2, 0.2, 0.45, 0.45], 0)["outcome"] == "fail"


# ---- G9 -------------------------------------------------------------------------------------------------------------------------

def cells(root, host, **filters):
    report = pg.evaluate_run(root, "baseline", host)
    return [c for c in report["cells"] if all(c[k] == v for k, v in filters.items())]


def test_g9_passes_cells_that_follow_the_window_rule_and_reach_t_min(run):
    found = cells(run.root, host_gates("h100"))
    assert len(found) == 4 and all(c["outcome"] == "pass" and c["reasons"] == [] for c in found)
    assert {(c["engine"], c["query"]) for c in found} == {("polars", "q01"), ("polars", "q03"), ("polars_gpu", "q01"), ("polars_gpu", "q03")}


def test_g9_calls_a_cell_below_resolution_when_the_run_is_shorter_than_t_min(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars",), per_run={("polars", "q01"): 0.5}).build()
    [cell] = cells(run.root, host_gates("h100"), query="q01")
    assert cell["outcome"] == "fail" and cell["reasons"] == ["per_run_below_t_min"]
    assert cells(run.root, host_gates("h100"), query="q03")[0]["outcome"] == "pass"
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False and report["ineligible"] == {"baseline/sf1/polars/q01": ["resolution_ok"]}


def test_g9_applies_the_hosts_own_t_min(tmp_path):
    run = RunBuilder(tmp_path / "run", host="a100", engines=("polars",), per_run={("polars", "q01"): 1.7, ("polars", "q03"): 2.5}).build()
    assert cells(run.root, host_gates("a100"), query="q01")[0]["reasons"] == ["per_run_below_t_min"]       # 1.7 s < 2 s
    assert cells(run.root, host_gates("a100"), query="q03")[0]["outcome"] == "pass"
    assert cells(run.root, host_gates("a100", t_min_s=1.5), query="q01")[0]["outcome"] == "pass"
    assert cells(run.root, host_gates("a100", t_min_s=2.5), query="q03")[0]["outcome"] == "pass", "equal to t_min is met"
    assert cells(run.root, host_gates("a100", t_min_s=2.6), query="q03")[0]["outcome"] == "fail"


def test_g9_calls_a_window_cut_by_the_cap_below_resolution(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars",), per_run={("polars", "q01"): 0.1}).build()
    [cell] = cells(run.root, host_gates("h100"), query="q01")
    assert cell["outcome"] == "fail" and set(cell["reasons"]) == {"window_below_10s", "per_run_below_t_min"}
    assert cell["median_trial"] == {"n": 50, "stop_reason": "max_runs"}


def retime(block, per_run, target=10.0):
    """Rewrite a window block as the stop rule makes it for runs of `per_run` seconds, keeping it consistent with its runs."""
    n = 1
    while n < 50 and math.fsum([per_run] * n) < target:
        n += 1
    total = math.fsum([per_run] * n)
    block["runs"] = [dict(block["runs"][0], run=i + 1, seconds=per_run) for i in range(n)]
    block.update(n=n, window_seconds=total, per_run_seconds=total / n, stop_reason="target_reached" if total >= target else "max_runs")


def test_g9_uses_the_median_trial_of_the_three(run):
    def set_trials(per_run_values):
        def change(results):
            trials = [r for r in results["records"] if r["phase"] == "measured" and r["query"] == "q01"]
            for record, value in zip(trials, per_run_values):
                retime(record["energy_window"], value)
        edit_results(run.root, "1", "polars", change)
    set_trials([1.5, 0.4, 1.4])
    assert cells(run.root, host_gates("h100"), engine="polars", query="q01")[0]["outcome"] == "pass"
    set_trials([0.4, 0.5, 1.5])
    assert cells(run.root, host_gates("h100"), engine="polars", query="q01")[0]["reasons"] == ["per_run_below_t_min"]
    set_trials([1.5, 1.4, 1.3])
    assert cells(run.root, host_gates("h100"), engine="polars", query="q01")[0]["outcome"] == "pass"


def test_g9_flags_a_window_that_did_not_follow_the_stop_rule(run):
    def drop_last_run(results):
        record = next(r for r in results["records"] if r["phase"] == "measured" and r["query"] == "q01")
        record["energy_window"]["runs"].pop()
    edit_results(run.root, "1", "polars", drop_last_run)
    [cell] = cells(run.root, host_gates("h100"), engine="polars", query="q01")
    assert cell["outcome"] == "fail" and "window_rule_violation" in cell["reasons"] and "stopped after" in cell["violations"][0]

    def run_too_long(results):
        record = next(r for r in results["records"] if r["phase"] == "measured" and r["query"] == "q03")
        runs = record["energy_window"]["runs"]
        runs.append(dict(runs[-1], run=len(runs) + 1))
    edit_results(run.root, "1", "polars", run_too_long)
    [cell] = cells(run.root, host_gates("h100"), engine="polars", query="q03")
    assert "window_rule_violation" in cell["reasons"] and "already sum to" in cell["violations"][0]


def test_g9_decides_from_the_runs_and_flags_a_summary_that_disagrees_with_them(run):
    """A block whose n, summed time or per-run time is not what its own runs give is a defect, and the runs decide."""
    def lie(results):
        record = next(r for r in results["records"] if r["phase"] == "measured" and r["query"] == "q01")
        record["energy_window"].update(per_run_seconds=9.0, window_seconds=40.0, n=3, stop_reason="max_runs")
    edit_results(run.root, "1", "polars", lie)
    [cell] = cells(run.root, host_gates("h100"), engine="polars", query="q01")
    assert cell["outcome"] == "fail" and cell["reasons"] == ["window_rule_violation"], "decided from the runs: 1.7 s per run passes t_min"
    assert any("n 3 but 6 runs" in v for v in cell["violations"]) and any("window_seconds" in v for v in cell["violations"])
    assert any("stop_reason" in v for v in cell["violations"])


def test_g9_flags_a_window_target_other_than_10_seconds(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars",), window_target=5.0).build()
    found = cells(run.root, host_gates("h100"))
    assert all("window_rule_violation" in c["reasons"] and any("window target" in v for v in c["violations"]) for c in found)


def test_window_rule_violation_is_the_rule_of_section_2_1():
    ok = lambda seconds: pg.window_rule_violation([{"seconds": s} for s in seconds])
    assert ok([10.0]) is None and ok([12.0]) is None, "n = 1 when the first run takes 10 s or longer"
    assert ok([4.0, 4.0, 4.0]) is None and ok([2.5] * 4) is None, "stops at the run that reaches 10 s"
    assert ok([4.0, 4.0]) is not None, "stopped at 8 s with runs left"
    assert ok([5.0, 5.0, 5.0]) is not None, "the first two runs already reached 10 s"
    assert ok([0.1] * 50) is None and ok([0.1] * 51) is not None, "the cap is 50 runs"
    assert ok([0.1] * 49) is not None
    assert ok([]) is not None and ok([0.0]) is not None and ok([-1.0, 20.0]) is not None
    assert pg.window_rule_violation([{"seconds": "a"}]) is not None


def test_the_window_rule_check_agrees_with_the_harness_stop_rule():
    import itertools
    import power_telemetry as pt
    for runs in itertools.chain(([1.0] * n for n in range(1, 14)), ([2.5] * n for n in range(1, 7)), ([10.0], [9.99, 0.02], [0.2] * 50,
                                                                                                      [0.2] * 49, [0.2] * 51, [3.0, 3.0, 3.0, 3.0])):
        mine = pg.window_rule_violation([{"seconds": s} for s in runs])
        theirs = pt.energy_window_stop_violation(runs, 10.0) if hasattr(pt, "energy_window_stop_violation") else mine
        assert (mine is None) == (theirs is None), runs


# ---- G10 -------------------------------------------------------------------------------------------------------------------------

def test_g10_passes_a_stable_idle_and_flags_an_unstable_one(tmp_path):
    stable = RunBuilder(tmp_path / "ok", host="h100", engines=("polars_gpu",), post_idle_scale=1.10).build()
    result = one(stable.root, host_gates("h100"), "G10", GPU)
    assert result["outcome"] == "pass" and 0.09 < result["relative_difference"]["S1-counter"] < 0.10
    unstable = RunBuilder(tmp_path / "bad", host="h100", engines=("polars_gpu", "polars"), post_idle_scale=1.11).build()
    result = one(unstable.root, host_gates("h100"), "G10", GPU)
    assert result["outcome"] == "fail" and result["unreliable_scopes"] == ["S1-counter"] and 0.10 < result["relative_difference"]["S1-counter"]
    report = pg.evaluate_round({"baseline": unstable.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is False, "above-idle is flagged, total is unaffected"
    assert report["ineligible"][GPU] == ["above_idle_reliable"] and report["gates"]["G6"]["fail"] == 0


def test_g10_is_symmetric_in_pre_and_post(tmp_path):
    low = RunBuilder(tmp_path / "low", host="h100", engines=("polars_gpu",), post_idle_scale=0.85).build()
    assert one(low.root, host_gates("h100"), "G10", GPU)["outcome"] == "fail"
    mild = RunBuilder(tmp_path / "mild", host="h100", engines=("polars_gpu",), post_idle_scale=0.92).build()
    assert one(mild.root, host_gates("h100"), "G10", GPU)["outcome"] == "pass"


def test_g10_needs_both_idle_windows(run):
    edit_results(run.root, "1", "polars_gpu", lambda r: r["idle_windows"].pop())
    result = one(run.root, host_gates("h100"), "G10", GPU)
    assert result["outcome"] == "fail" and "P_idle cannot be formed" in result["problems"][0] and result["unreliable_scopes"] == ["all"]


def test_g10_fails_when_the_counter_gave_no_idle_power(run):
    edit_results(run.root, "1", "polars_gpu", lambda r: r["idle_windows"][1]["gpu_energy"].update(delta_mj=None, error="x"))
    result = one(run.root, host_gates("h100"), "G10", GPU)
    assert result["outcome"] == "fail" and "S1-counter" in result["unreliable_scopes"]


def test_g10_judges_the_rapl_scopes_of_the_cpu_only_host(tmp_path):
    steady = RunBuilder(tmp_path / "steady", host="c-gcp", engines=("polars",)).build()
    result = one(steady.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")
    assert result["outcome"] == "pass" and set(result["relative_difference"]) == {"S2-all", "S2-pinned"}
    drifted = RunBuilder(tmp_path / "drift", host="c-gcp", engines=("polars",), post_idle_scale=1.5).build()
    result = one(drifted.root, host_gates("c-gcp"), "G10", "baseline/sf1/polars")
    assert result["outcome"] == "fail" and "S2-pinned" in result["unreliable_scopes"] and result["relative_difference"]["S2-pinned"] > 0.10


def test_g10_on_gh200_uses_the_module_scope_tag(tmp_path):
    run = RunBuilder(tmp_path / "run", host="gh200", engines=("polars_gpu",), post_idle_scale=1.3).build()
    result = one(run.root, host_gates("gh200"), "G10", GPU)
    assert result["unreliable_scopes"] == ["S1-module"]


# ---- G11 -------------------------------------------------------------------------------------------------------------------------

def test_g11_passes_when_throttle_reasons_temperature_and_the_power_text_are_logged(run):
    result = one(run.root, host_gates("h100"), "G11", GPU)
    assert result["outcome"] == "pass" and result["power_limit_text_logged"] == ["end", "start"] and result["throttle_samples_with_reasons"] == 0


def test_g11_fails_without_throttle_reasons_or_temperature_in_gpu_csv(run):
    run.write_gpu_csv(drop=("clocks_event_reasons.active",))
    assert any("throttle-reason samples" in p for p in one(run.root, host_gates("h100"), "G11", GPU)["problems"])
    run.write_gpu_csv(drop=("temperature.gpu",))
    assert any("temperature.gpu samples" in p for p in one(run.root, host_gates("h100"), "G11", GPU)["problems"])
    run.write_gpu_csv()
    assert one(run.root, host_gates("h100"), "G11", GPU)["outcome"] == "pass"


def test_g11_accepts_the_older_throttle_column_name(run):
    text = (run.root / "gpu.csv").read_text().replace("clocks_event_reasons.active", "clocks_throttle_reasons.active")
    (run.root / "gpu.csv").write_text(text)
    assert one(run.root, host_gates("h100"), "G11", GPU)["outcome"] == "pass"


@pytest.mark.parametrize("key", [pg.POWER_STATE_ITEM_KEY, pg.PERFORMANCE_STATE_ITEM_KEY])
def test_g11_fails_when_a_state_capture_is_missing_unreadable_or_altered(run, key):
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i[key].pop("end"))
    assert any("not captured" in p for p in one(run.root, host_gates("h100"), "G11", GPU)["problems"])
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i[key].update(end=dict(i[key]["start"], file="gone.txt")))
    assert any("gone.txt is missing" in p for p in one(run.root, host_gates("h100"), "G11", GPU)["problems"])
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i[key].update(end=dict(i[key]["start"], sha256="0" * 64)))
    assert any("sha256 differs" in p for p in one(run.root, host_gates("h100"), "G11", GPU)["problems"])
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i[key]["start"].update(returncode=1))
    assert any("not captured" in p for p in one(run.root, host_gates("h100"), "G11", GPU)["problems"])


def test_g11_needs_a_current_power_limit_in_the_power_text(run):
    path = run.root / "sf1" / "polars_gpu-gpu-power-start.txt"
    text = path.read_text().replace("Current Power Limit", "Power Limit")
    path.write_text(text)
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i[pg.POWER_STATE_ITEM_KEY]["start"].update(sha256=__import__("hashlib").sha256(text.encode()).hexdigest()))
    assert any("holds no Current Power Limit" in p for p in one(run.root, host_gates("h100"), "G11", GPU)["problems"])


def test_g11_counts_observed_throttling_without_judging_it(run):
    lines = (run.root / "gpu.csv").read_text().splitlines()
    lines = [l if (i == 0 or i % 7) else l.rsplit(",", 1)[0] + ", 0x0000000000000008" for i, l in enumerate(lines)]       # HW slowdown bit
    (run.root / "gpu.csv").write_text("\n".join(lines) + "\n")
    result = one(run.root, host_gates("h100"), "G11", GPU)
    assert result["outcome"] == "pass" and result["throttle_samples_with_reasons"] > 10


def test_g11_is_not_applicable_on_the_cpu_only_host(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-gcp", engines=("polars",)).build()
    assert one(run.root, host_gates("c-gcp"), "G11", "baseline/sf1/polars")["outcome"] == "not_applicable"


# ---- G12 -------------------------------------------------------------------------------------------------------------------------

def test_g12_fails_the_round_on_a_foreign_compute_process_at_launch(run):
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i["gpu_idle_gate"].update(idle=False, compute_apps=["4242, python3, 512"]))
    result = one(run.root, host_gates("h100"), "G12", GPU)
    assert result["outcome"] == "fail" and len(result["problems"]) == 2
    report = pg.evaluate_round({"baseline": run.root}, host_gates("h100"), clean_captures())
    assert report["round_failed"] is True and report["round_failures"][0].startswith("G12 baseline/sf1/polars_gpu")


def test_g12_fails_when_the_compute_processes_could_not_be_listed(run):
    edit_schedule_item(run.root, "1", "polars", lambda i: i["gpu_idle_gate"].update(error="nvidia-smi timed out"))
    assert one(run.root, host_gates("h100"), "G12", POLARS)["problems"] == ["nvidia-smi could not list the compute processes at launch"]
    edit_schedule_item(run.root, "1", "polars", lambda i: i.pop("gpu_idle_gate"))
    assert "no gpu_idle_gate record" in one(run.root, host_gates("h100"), "G12", POLARS)["problems"][0]


def test_g12_looks_at_the_gpu_after_the_worker_exited(run):
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i[pg.COMPUTE_APPS_END_KEY].update(compute_apps=["99, other, 10"]))
    assert one(run.root, host_gates("h100"), "G12", GPU)["problems"] == ["a compute process was on the GPU after the worker exited"]
    edit_schedule_item(run.root, "1", "polars_gpu", lambda i: i.pop(pg.COMPUTE_APPS_END_KEY))
    assert "no compute-process listing" in one(run.root, host_gates("h100"), "G12", GPU)["problems"][0]


def test_g12_round_level_on_a_gpu_host_needs_both_captures_and_an_empty_gpu(run):
    host = host_gates("h100")
    assert pg.round_exclusivity(host, clean_captures())["outcome"] == "pass"
    assert "both start and end are needed" in pg.round_exclusivity(host, clean_captures()[:1])["problems"][0]
    busy = clean_captures()
    busy[1]["gpu_compute_apps"] = {"returncode": 0, "apps": ["1, foreign, 100"]}
    result = pg.round_exclusivity(host, busy)
    assert result["outcome"] == "fail" and "end: foreign GPU compute process" in result["problems"][0]
    unlisted = clean_captures()
    unlisted[0]["gpu_compute_apps"] = {"returncode": 9, "apps": None}
    assert "could not be listed" in pg.round_exclusivity(host, unlisted)["problems"][0]
    report = pg.evaluate_round({"baseline": run.root}, host, busy)
    assert report["round_failed"] is True and any(f.startswith("G12 round:") for f in report["round_failures"])


def test_g12_on_the_cpu_only_host_compares_scheduled_work_with_the_allow_list(tmp_path):
    host = host_gates("c-gcp")
    ok = clean_captures(gpu_compute_apps=None)
    assert pg.round_exclusivity(host, ok)["outcome"] == "pass"
    extra = clean_captures(gpu_compute_apps=None)
    extra[1]["scheduled"] = {"systemd_timers": ["fstrim.timer", "apt-daily.timer"], "cron_files": ["backup"], "crontab_entries": ["* * * * * x"], "at_jobs": ["1 Mon"]}
    result = pg.round_exclusivity(host, extra)
    assert result["outcome"] == "fail" and "end: scheduled work not in the allow-list" in result["problems"][0]
    for item in ("timer:apt-daily.timer", "cron:backup", "crontab:* * * * * x", "at:1 Mon"):
        assert item in result["problems"][0]
    allowed = host_gates("c-gcp", allowed_scheduled=["timer:fstrim.timer", "timer:apt-daily.timer", "cron:backup", "crontab:* * * * * x", "at:1 Mon"])
    assert pg.round_exclusivity(allowed, extra)["outcome"] == "pass"
    broken = clean_captures(gpu_compute_apps=None)
    broken[0]["scheduled"]["systemd_timers"] = None
    assert "a scheduler listing failed" in pg.round_exclusivity(host, broken)["problems"][0]


def test_host_exclusivity_capture_parses_what_the_commands_print():
    answers = {
        "nvidia-smi": {"returncode": 0, "stdout": "123, python, 1024\n\n"},
        "systemctl": {"returncode": 0, "stdout": "Mon 2026-11-16 00:00:00 UTC 6h left Sun 2026-11-15 n/a fstrim.timer fstrim.service\napt-daily.timer apt-daily.service\n"},
        "sh": {"returncode": 0, "stdout": "/etc/cron.d:\ne2scrub_all\n.placeholder\n\n/etc/cron.daily:\nlogrotate\nREADME\n"},
        "crontab": {"returncode": 1, "stdout": ""},
        "atq": {"returncode": 0, "stdout": "3\tMon Nov 16 10:00:00 2026 a user\n"}}
    capture = pg.capture_host_exclusivity("start", gpu=True, run=lambda command: answers[command[0]], now=lambda: __import__("datetime").datetime(2026, 11, 16))
    assert capture["gpu_compute_apps"] == {"returncode": 0, "apps": ["123, python, 1024"]}
    assert capture["scheduled"] == {"systemd_timers": ["apt-daily.timer", "fstrim.timer"], "cron_files": ["e2scrub_all", "logrotate"],
                                    "crontab_entries": [], "at_jobs": ["3\tMon Nov 16 10:00:00 2026 a user"]}
    assert pg.scheduled_items(capture) == ["at:3\tMon Nov 16 10:00:00 2026 a user", "cron:e2scrub_all", "cron:logrotate", "timer:apt-daily.timer", "timer:fstrim.timer"]
    cpu_only = pg.capture_host_exclusivity("end", gpu=False, run=lambda command: {"returncode": None, "stdout": "", "error": "OSError"})
    assert cpu_only["gpu_compute_apps"] is None and cpu_only["scheduled"]["systemd_timers"] is None


def test_the_capture_command_prints_the_allow_list_items(monkeypatch, capsys):
    monkeypatch.setattr(pg, "capture_host_exclusivity", lambda label, gpu, **kw: {"label": label, "gpu": gpu, "scheduled": {
        "systemd_timers": ["fstrim.timer"], "cron_files": ["logrotate"], "crontab_entries": [], "at_jobs": []}})
    assert pg.main(["--capture-exclusivity", "--gpu"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["allow_list_items"] == ["cron:logrotate", "timer:fstrim.timer"] and printed["capture"]["gpu"] is True
    with pytest.raises(SystemExit):
        pg.main(["--host", "h100"])


def test_the_capture_degrades_without_the_tools(monkeypatch):
    monkeypatch.setattr(pg.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("nope")))
    capture = pg.capture_host_exclusivity("start", gpu=True)
    assert capture["gpu_compute_apps"]["apps"] is None and capture["scheduled"]["systemd_timers"] is None


# ---- hosts that measure nothing --------------------------------------------------------------------------------------------------

def test_a_time_only_host_has_no_energy_gates(tmp_path):
    run = RunBuilder(tmp_path / "run", host="c-vm", engines=("polars", "spark_cpu")).build()
    report = pg.evaluate_round({"baseline": run.root}, host_gates("c-vm"), [])
    assert report["round_failed"] is False
    for session in report["runs"][0]["sessions"]:
        assert {r["outcome"] for r in session["gates"].values()} == {"not_applicable"}
    assert report["runs"][0]["cells"] == [] and report["round_exclusivity"]["outcome"] == "not_applicable"


# ---- the command line --------------------------------------------------------------------------------------------------------------

def test_the_command_line_exit_codes(run, tmp_path, capsys):
    config = tmp_path / "hosts.json"
    config.write_text(json.dumps({"format": 1, "hosts": {"h100": HOST_ENTRIES["h100"], "gh200": dict(HOST_ENTRIES["gh200"], t_min_s=None)}}))
    captures = []
    for capture in clean_captures():
        path = tmp_path / f"{capture['label']}.json"
        path.write_text(json.dumps(capture))
        captures += ["--host-capture", str(path)]
    out = tmp_path / "report.json"
    argv = ["--host", "h100", "--config", str(config), "--run-dir", str(run.root), "--suite", "baseline", *captures, "--output", str(out)]
    assert pg.main(argv) == 0
    assert json.loads(out.read_text())["round_failed"] is False
    edit_results(run.root, "1", "polars_gpu", lambda r: r[pg.COUNTER_BLOCK].update(source="x"))
    assert pg.main(argv) == 1 and json.loads(out.read_text())["round_failures"]
    assert pg.main(["--host", "gh200", "--config", str(config), "--run-dir", str(run.root)]) == 2
    assert "unset in the host config: t_min_s" in capsys.readouterr().err
    assert pg.main(["--host", "h100", "--config", str(tmp_path / "none.json"), "--run-dir", str(run.root)]) == 2
    assert pg.main(["--host", "h100", "--config", str(config), "--run-dir", str(tmp_path / "empty")]) == 2


def test_the_gates_do_not_carry_their_own_copy_of_the_design_numbers_wrongly():
    import paper3_campaign
    import power_telemetry
    assert pg.WINDOW_TARGET_S == paper3_campaign.WINDOW_S == 10.0 and pg.WINDOW_MAX_RUNS == power_telemetry.ENERGY_WINDOW_MAX_RUNS == 50
    assert (pg.G7_MIN_COVERAGE, pg.G7_MAX_GAP_S, pg.G7_MIN_WINDOW_S) == (0.80, 1.0, 10.0)
    assert (pg.G8_MEDIAN_LAG_S, pg.G8_DRIFT_S, pg.G8_UTILIZATION_PERCENT, pg.G8_MIN_WINDOW_S) == (0.4, 0.2, 10.0, 5.0)
    assert pg.G10_MAX_REL_DIFF == 0.10 and pg.ROUND_FAILING == ("G6", "G12")
