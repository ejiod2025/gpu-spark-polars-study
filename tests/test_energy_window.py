"""Paper 3 window rule and scope tags in worker.py and power_telemetry.py (docs/PAPER3_ENERGY_DESIGN.md sections 2.1, 2.3, 2.4).

Nothing here needs a GPU: the NVML counter is a fake that advances on every read (so the gaps between runs are visible in
it, as on a real host), the engine is Polars on a three-row table or a fake Spark session, and queries are the two-query fake
suite of test_power_telemetry.py. Paper 1 invariance (no new flag, same records) is in test_paper1_invariance.py.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import power_telemetry as pt  # noqa: E402
import preflight  # noqa: E402
import worker  # noqa: E402
from test_power_telemetry import (  # noqa: E402,F401  (fixtures are imported by name so pytest registers them here)
    FakeEnergyReader, FakeNvml, FakeSparkFrame, hermetic_gpu_energy, install_fake_spark, install_fake_suite, run_worker,
    worker_data)

class ScopedReader(FakeEnergyReader):
    """FakeEnergyReader as worker.py builds it in Paper 3 mode: with the declared scope."""

    def __init__(self, device, scope=None):
        super().__init__(device)
        self.scope = scope

    def describe(self):
        return pt.GpuEnergyReader(self.device, nvml=FakeNvml(), which=lambda name: None, scope=self.scope).describe()


@pytest.fixture
def scoped(monkeypatch):
    monkeypatch.setattr(worker, "GpuEnergyReader", ScopedReader)


NO_IDLE = ("--session-idle-seconds", "0")      # the in-session idle windows default to 2 x (60 s + 30 s) in Paper 3 mode: tests opt out


def window_run(monkeypatch, tmp_path, data, engine="polars", runs=2, scope="board", target="5", extra=()):
    idle = () if "--session-idle-seconds" in extra else NO_IDLE
    return run_worker(monkeypatch, tmp_path, data, engine, runs, "--gpu-device", "0", "--gpu-energy-scope", scope,
                      "--energy-window-s", target, *idle, *extra)


class ScriptedClock:
    """worker.time with a perf_counter whose timed regions last exactly the scripted seconds.

    One query (--queries qa) makes two perf_counter calls for the session set-up, then two per run (start, end): the script is
    the duration of the warm-up, then of every window run; the last value repeats when the script runs out."""

    def __init__(self, monkeypatch, durations):
        import itertools
        import time as real
        pairs = ((0.0, d) for d in durations)
        self.steps = itertools.chain([0.0, 0.0], itertools.chain.from_iterable(pairs), itertools.cycle((0.0, durations[-1])))
        self.now = 0.0
        clone = types.SimpleNamespace(**{name: getattr(real, name) for name in dir(real) if not name.startswith("__")})
        clone.perf_counter = self.perf_counter
        monkeypatch.setattr(worker, "time", clone)

    def perf_counter(self):
        self.now += next(self.steps)
        return self.now


def scripted(monkeypatch, tmp_path, data, durations, target):
    ScriptedClock(monkeypatch, durations)
    return window_run(monkeypatch, tmp_path, data, runs=1, target=target, extra=("--queries", "qa"))


def measured(result):
    return [r for r in result["records"] if r["phase"] == "measured"]


# ---- the rule -----------------------------------------------------------------------------------------------

def runs_made(seconds, target, max_runs=50):
    """n the stop rule makes for a sequence of per-run times: the first prefix after which the window may stop."""
    for n in range(1, len(seconds) + 1):
        if pt.energy_window_complete(seconds[:n], target, max_runs):
            return n
    return None


@pytest.mark.parametrize("seconds, target, n", [
    ([30.0], 10.0, 1), ([10.0], 10.0, 1), ([9.99, 0.02], 10.0, 2), ([9.99, 0.01], 10.0, 2), ([0.1] * 200, 10.0, 50),
    ([2.0] * 10, 10.0, 5), ([2.0, 2.0, 2.0, 2.0, 2.5], 10.0, 5), ([5.0, 5.0, 5.0], 10.0, 2), ([0.5, 0.5], 1.0, 2),
    ([0.1, 0.1, 0.1, 6.0, 9.0], 5.0, 4), ([0.001] * 60, 10.0, 50), ([0.2] * 100, 10.0, 50), ([0.3] * 100, 10.0, 34)])
def test_window_stop_rule_edges(seconds, target, n):
    assert runs_made(seconds, target) == n
    assert pt.energy_window_stop_violation(seconds[:n], target) is None            # what the rule makes is what the gate accepts


def test_window_stop_rule_details():
    assert pt.energy_window_complete([], 10.0) is False and pt.energy_window_complete([9.99], 10.0) is False
    assert pt.energy_window_complete([10.0], 10.0) is True                         # reaching the target exactly stops the window
    assert pt.energy_window_complete([0.001] * 49, 10.0) is False and pt.energy_window_complete([0.001] * 50, 10.0) is True
    assert pt.energy_window_complete([1.0] * 7, 100.0, max_runs=7) is True
    assert pt.ENERGY_WINDOW_MAX_RUNS == 50
    assert pt.energy_window_complete([4.0, 4.0, 2.0], 10.0) is True and pt.energy_window_complete([4.0, 4.0, 1.9], 10.0) is False


@pytest.mark.parametrize("seconds, target, text", [
    ([], 10.0, "no run"), ([1.0, 1.0, 1.0], 10.0, "stopped after 3 runs"), ([6.0, 6.0, 6.0], 10.0, "should have stopped"),
    ([10.0, 1.0], 10.0, "should have stopped"), ([0.1] * 51, 100.0, "exceed the cap"), ([1.0, 0.0], 10.0, "positive finite"),
    ([1.0, float("nan")], 10.0, "positive finite"), ([1.0], 0, "target_seconds")])
def test_window_stop_violations_are_named(seconds, target, text):
    assert text in pt.energy_window_stop_violation(seconds, target)


def test_a_window_at_the_cap_may_stay_below_the_target_and_one_at_the_target_may_stop_early():
    assert pt.energy_window_stop_violation([0.01] * 50, 10.0) is None              # cap reached: below the target is allowed
    assert pt.energy_window_stop_violation([0.01] * 49, 10.0) is not None          # not the cap, not the target
    assert pt.energy_window_stop_violation([3.0, 3.0, 4.5], 10.0) is None


@pytest.mark.parametrize("bad", [0, -1.0, float("nan"), float("inf"), None, "2", True])
def test_the_rule_is_never_applied_to_a_time_it_cannot_be_applied_to(bad):
    with pytest.raises(ValueError):
        pt.energy_window_complete([1.0], bad)
    with pytest.raises(ValueError):
        pt.energy_window_complete([bad], 10.0)


# ---- aggregation: window sums, gaps excluded ----------------------------------------------------------------

def synthetic_runs(deltas, seconds, gap_mj=10_000, source=pt.GPU_ENERGY_NVML, scope="board"):
    """Per-run entries as the worker keeps them, on a counter that also advances `gap_mj` between runs."""
    entries, counter, clock = [], 1_000_000, 0
    for index, (delta, secs) in enumerate(zip(deltas, seconds), start=1):
        start_mj = counter
        counter += delta if delta is not None else 0
        window = pt.gpu_energy_window(source, 0, {"mj": start_mj, "monotonic_ns": [clock, clock + 1]},
                                      {"mj": None if delta is None else counter, "monotonic_ns": [clock + 10, clock + 11]})
        entries.append({"run": index, "seconds": secs, "timed_start_utc": f"2026-01-01T00:00:{index:02d}.000000+00:00",
                        "timed_end_utc": f"2026-01-01T00:00:{index:02d}.500000+00:00", "timed_start_monotonic_ns": clock + 2,
                        "timed_end_monotonic_ns": clock + 2 + int(secs * 1e9), "gpu_energy": pt.tag_gpu_energy(window, scope)})
        counter += gap_mj                                  # cleanup, plan serialisation: energy the window must not include
        clock += int(secs * 1e9) + 50_000_000
    return entries


def test_window_energy_is_the_sum_of_the_runs_and_excludes_every_gap():
    entries = synthetic_runs([1000, 2000, 1500], [1.0, 1.2, 0.9])
    block = pt.aggregate_energy_window(entries, scope="board", source=pt.GPU_ENERGY_NVML, device=0,
                                       target_seconds=10.0, t_hat_seconds=3.3)
    first, last = entries[0]["gpu_energy"], entries[-1]["gpu_energy"]
    assert last["end_mj"] - first["start_mj"] == 4500 + 2 * 10_000       # what an end-minus-start window would claim
    energy = block["gpu_energy"]
    assert energy["window_delta_mj"] == 4500 and energy["per_run_delta_mj"] == 1500.0 and energy["valid_runs"] == 3
    assert block["n"] == 3 and block["window_seconds"] == pytest.approx(3.1) and block["per_run_seconds"] == pytest.approx(3.1 / 3)
    assert block["below_target"] is True and block["stop_reason"] == "max_runs"      # 3.1 s of runs against a 10 s target
    assert "t_hat" not in block["rule"] and "QA only" in block["t_hat_source"] and block["format"] == pt.ENERGY_WINDOW_FORMAT == 2
    assert block["span"]["seconds"] > block["window_seconds"] and block["span"]["gap_seconds"] > 0
    assert block["span"]["start_monotonic_ns"] == entries[0]["timed_start_monotonic_ns"]
    assert block["span"]["end_monotonic_ns"] == entries[-1]["timed_end_monotonic_ns"]
    assert [r["run"] for r in block["runs"]] == [1, 2, 3]


def test_cpu_energy_is_integrated_over_the_runs_not_over_the_span(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=1)
    for record in result["records"]:
        windows = pt.record_run_windows(record)
        if record["phase"] == "warmup":
            assert windows == [pt.record_window(record)]
            continue
        runs = record["energy_window"]["runs"]
        assert len(windows) == len(runs) > 1 and {w["basis"] for w in windows} == {"timed"}
        assert [w["start_monotonic_ns"] for w in windows] == [r["timed_start_monotonic_ns"] for r in runs]
        assert all(a["end_monotonic_ns"] < b["start_monotonic_ns"] for a, b in zip(windows, windows[1:]))     # gaps stay outside
        covered = sum(w["end_monotonic_ns"] - w["start_monotonic_ns"] for w in windows) / 1e9
        assert covered == pytest.approx(record["energy_window"]["window_seconds"], abs=1e-3)
        assert covered < (record["timed_end_monotonic_ns"] - record["timed_start_monotonic_ns"]) / 1e9
    assert pt.record_run_windows({}) == [pt.record_window({})] and pt.record_run_windows(None) == [pt.record_window(None)]
    assert pt.record_run_windows({"energy_window": {"runs": "junk"}})[0]["basis"] == "legacy"


def test_one_unreadable_run_voids_the_window_energy_but_not_its_time():
    entries = synthetic_runs([1000, None, 1500], [1.0, 1.0, 1.0])
    block = pt.aggregate_energy_window(entries, scope="board", source=pt.GPU_ENERGY_NVML, device=0,
                                       target_seconds=2.0, t_hat_seconds=1.0)
    energy = block["gpu_energy"]
    assert energy["window_delta_mj"] is None and energy["per_run_delta_mj"] is None
    assert energy["valid_runs"] == 2 and energy["invalid_runs"] == [2] and energy["scope_tag"] == "none"
    assert block["window_seconds"] == 3.0 and block["below_target"] is False and block["stop_reason"] == "target_reached"


def test_a_single_run_window_is_still_a_window():
    block = pt.aggregate_energy_window(synthetic_runs([700], [12.0]), scope="module", source=pt.GPU_ENERGY_NVML, device=0,
                                       target_seconds=10.0, t_hat_seconds=12.0)
    assert block["n"] == 1 and block["per_run_seconds"] == 12.0 and block["gpu_energy"]["per_run_delta_mj"] == 700.0
    assert block["stop_reason"] == "target_reached"
    assert block["gpu_energy"]["scope_tag"] == "S1-module" and block["span"]["gap_seconds"] == pytest.approx(0.0, abs=1e-6)


def test_no_counter_means_no_energy_and_the_tag_none():
    entries = [{k: v for k, v in e.items() if k != "gpu_energy"} for e in synthetic_runs([1, 1], [1.0, 1.0])]
    for scope, source in (("none", "unavailable"), ("board", pt.GPU_ENERGY_UNAVAILABLE)):
        block = pt.aggregate_energy_window(entries, scope=scope, source=source, device=0, target_seconds=1.0, t_hat_seconds=1.0)
        energy = block["gpu_energy"]
        assert energy["scope_tag"] == "none" and energy["window_delta_mj"] is None and energy["valid_runs"] == 0
        assert "invalid_runs" not in energy


def test_an_empty_window_is_refused():
    with pytest.raises(ValueError):
        pt.aggregate_energy_window([], scope="board", source="x", device=0, target_seconds=1.0, t_hat_seconds=1.0)


# ---- scope tags -----------------------------------------------------------------------------------------------

def test_scope_tag_vocabulary_and_per_scope_tags():
    assert pt.SCOPE_TAGS == ("S1-counter", "S1-sampled", "S1-module", "S2-pinned", "S2-all", "S3", "none")
    assert pt.GPU_SCOPE_TAG == {"board": "S1-counter", "module": "S1-module", "none": "none"}
    assert pt.gpu_energy_scope_tag("board", pt.GPU_ENERGY_NVML) == "S1-counter"
    assert pt.gpu_energy_scope_tag("module", pt.GPU_ENERGY_CTYPES) == "S1-module"
    assert pt.gpu_energy_scope_tag("module", pt.GPU_ENERGY_SMI) == "S1-module"            # a counter read through nvidia-smi is a counter
    assert pt.gpu_energy_scope_tag("board", pt.GPU_ENERGY_UNAVAILABLE) == "none"
    assert pt.gpu_energy_scope_tag("none", pt.GPU_ENERGY_NVML) == "none"
    assert pt.gpu_energy_scope_tag("board", pt.GPU_ENERGY_NVML, has_value=False) == "none"
    with pytest.raises(ValueError):
        pt.gpu_energy_scope_tag("auto", pt.GPU_ENERGY_NVML)


def test_an_untagged_or_unknown_value_is_refused():
    assert pt.require_scope_tag({"scope_tag": "S2-pinned"}) == "S2-pinned" and pt.require_scope_tag("S3") == "S3"
    for bad in ({}, {"scope_tag": "board"}, {"scope_tag": None}, "S1", None, 3):
        with pytest.raises(ValueError):
            pt.require_scope_tag(bad)


def test_tagging_leaves_the_value_untouched_and_tags_a_failed_read_none():
    good = pt.gpu_energy_window(pt.GPU_ENERGY_NVML, 0, {"mj": 10, "monotonic_ns": [1, 2]}, {"mj": 40, "monotonic_ns": [3, 4]})
    tagged = pt.tag_gpu_energy(good, "module")
    assert tagged["scope_tag"] == "S1-module" and {k: v for k, v in tagged.items() if k != "scope_tag"} == good
    lost = pt.gpu_energy_window(pt.GPU_ENERGY_NVML, 0, {"mj": 10, "monotonic_ns": [1, 2]}, {"mj": None, "monotonic_ns": [3, 4]})
    assert pt.tag_gpu_energy(lost, "board")["scope_tag"] == "none"
    assert pt.tag_gpu_energy(pt.gpu_energy_window(pt.GPU_ENERGY_UNAVAILABLE, 0, {}, {}), "board")["scope_tag"] == "none"


def test_describe_follows_the_declared_scope_and_is_the_original_without_one():
    def describe(scope):
        return pt.GpuEnergyReader(0, nvml=FakeNvml(), which=lambda name: None, scope=scope).describe()
    plain, board, module = describe(None), describe("board"), describe("module")
    assert plain["semantics"].startswith("cumulative whole-board energy") and "scope" not in plain and "scope_tag" not in plain
    assert list(plain) == ["source", "unit", "device_index", "reasons", "semantics"]
    assert board["semantics"] == plain["semantics"] and board["scope"] == "board" and board["scope_tag"] == "S1-counter"
    assert "whole-board" not in module["semantics"] and "whole-module" in module["semantics"] and "Grace" in module["semantics"]
    assert module["scope"] == "module" and module["scope_tag"] == "S1-module"
    unavailable = pt.GpuEnergyReader(0, nvml=FakeNvml(not_supported=True), which=lambda name: None, scope="module").describe()
    assert unavailable["source"] == "unavailable" and unavailable["scope_tag"] == "none"
    with pytest.raises(ValueError):
        pt.GpuEnergyReader(0, nvml=FakeNvml(), scope="gpu")


def test_the_probe_cli_words_the_report_by_the_declared_scope(monkeypatch, capsys):
    monkeypatch.setattr(pt.CtypesNvml, "LIBRARIES", ("libnot-installed.so.1",))
    monkeypatch.setattr(pt.importlib, "import_module", lambda name: FakeNvml())
    assert pt.main(["--probe-gpu-energy", "--duration", "0.05", "--gpu-energy-scope", "module"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["scope"] == "module" and out["scope_tag"] == "S1-module" and "whole-module" in out["semantics"]
    assert pt.main(["--probe-gpu-energy", "--duration", "0.05"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert "scope" not in out and "whole-board" in out["semantics"]


def test_cpu_energy_scope_tags_follow_the_sampler_report():
    pinned = {"cpu_energy_source": "powercap-rapl", "node_power_source": None,
              "pinning": {"attribution": {"possible": True, "pinned_sockets": [0], "pinned_zones": {"0": {"package": ["a"]}}}}}
    assert set(pt.cpu_energy_scope_tags(pinned)) == {"S2-all", "S2-pinned"}
    assert pt.cpu_energy_scope_tags(pinned)["S2-pinned"]["pinned_sockets"] == [0]
    unpinned = {"cpu_energy_source": "powercap-rapl", "node_power_source": "ipmi-dcmi"}
    assert set(pt.cpu_energy_scope_tags(unpinned)) == {"S2-all", "S3"}
    assert set(pt.cpu_energy_scope_tags({"cpu_energy_source": "perf-stat"})) == {"S2-all"}
    assert set(pt.cpu_energy_scope_tags({"cpu_energy_source": None, "node_power_source": None})) == {"none"}
    assert set(pt.cpu_energy_scope_tags({})) == {"none"}
    for tags in (pinned, unpinned, {}):
        assert set(pt.cpu_energy_scope_tags(tags)) <= set(pt.SCOPE_TAGS)


# ---- the worker in window mode --------------------------------------------------------------------------------

def test_a_measured_trial_is_a_window_of_back_to_back_runs_that_follow_the_stop_rule(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data)
    warm = {r["query"]: r for r in result["records"] if r["phase"] == "warmup"}
    rows = measured(result)
    assert len(result["records"]) == 2 + 4 and len(rows) == 4                    # one record per (query, trial), as in Paper 1
    assert result["energy_window_rule"]["target_seconds"] == 5 and result["energy_window_rule"]["max_runs"] == 50
    for record in rows:
        block = record["energy_window"]
        assert block["t_hat_seconds"] == warm[record["query"]]["seconds"]         # recorded for QA: the query's warmup time
        assert block["target_seconds"] == 5 and block["n"] == len(block["runs"]) > 1
        assert pt.energy_window_stop_violation([r["seconds"] for r in block["runs"]], 5) is None
        assert block["stop_reason"] == "max_runs" and block["below_target"] is True   # 50 runs of a millisecond cannot reach 5 s
        assert block["window_seconds"] == pytest.approx(sum(r["seconds"] for r in block["runs"]))
        assert record["seconds"] == block["per_run_seconds"] == pytest.approx(block["window_seconds"] / block["n"])
        assert "gpu_energy" not in record                                         # the window replaces the single-run value
        previous_end = 0
        for index, run in enumerate(block["runs"], start=1):                      # stamps exactly as the single-run path
            assert run["run"] == index
            assert previous_end < run["timed_start_monotonic_ns"] < run["timed_end_monotonic_ns"]
            previous_end = run["timed_end_monotonic_ns"]
            energy = run["gpu_energy"]
            assert energy["start_read_monotonic_ns"][1] <= run["timed_start_monotonic_ns"]       # reads just outside the timer
            assert run["timed_end_monotonic_ns"] <= energy["end_read_monotonic_ns"][0]
        assert record["timed_start_monotonic_ns"] == block["runs"][0]["timed_start_monotonic_ns"]
        assert record["timed_end_monotonic_ns"] == block["runs"][-1]["timed_end_monotonic_ns"]
        assert record["rows"] == warm[record["query"]]["rows"]                    # run 1's rows, for the reference comparison
        assert (tmp_path / "out-polars" / record["plan"]).is_file()
    plans = sorted(p.name for p in (tmp_path / "out-polars" / "plans").iterdir())
    assert plans == sorted(f"{q}-{ph}-{t}.txt" for q in ("qa", "qb") for ph, t in (("warmup", 0), ("measured", 1), ("measured", 2)))


def test_window_energy_excludes_the_gaps_the_counter_advanced_through(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, scope="board")
    for record in measured(result):
        block = record["energy_window"]
        n, energy = block["n"], block["gpu_energy"]
        assert all(run["gpu_energy"]["delta_mj"] == 2500 for run in block["runs"])
        assert energy["window_delta_mj"] == 2500 * n and energy["per_run_delta_mj"] == 2500.0 and energy["valid_runs"] == n
        first, last = block["runs"][0]["gpu_energy"], block["runs"][-1]["gpu_energy"]
        assert last["end_mj"] - first["start_mj"] == 2500 * n + 2500 * (n - 1)          # the gaps are in the counter, not the window
        assert block["span"]["gap_seconds"] > 0 and block["span"]["seconds"] > block["window_seconds"]
        assert pt.energy_window_per_run_j(record) == (2.5, "S1-counter")
        assert pt.gpu_energy_delta_j(record) is None                                       # the Paper 1 helper never sees a window


@pytest.mark.parametrize("scope, tag", [("board", "S1-counter"), ("module", "S1-module")])
def test_every_energy_value_carries_the_declared_scope_tag(monkeypatch, tmp_path, worker_data, scoped, scope, tag):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, scope=scope)
    assert result["energy_scope"]["gpu_energy_scope"] == scope and result["energy_scope"]["gpu_counter_scope_tag"] == tag
    assert result["gpu_energy_counter"]["scope"] == scope and result["gpu_energy_counter"]["scope_tag"] == tag
    warmups = [r for r in result["records"] if r["phase"] == "warmup"]
    assert len(warmups) == 2 and all(r["gpu_energy"]["scope_tag"] == tag and "energy_window" not in r for r in warmups)
    for record in measured(result):
        block = record["energy_window"]
        assert pt.require_scope_tag(block["gpu_energy"]) == tag
        assert all(pt.require_scope_tag(run["gpu_energy"]) == tag for run in block["runs"])
        assert pt.energy_window_per_run_j(record)[1] == tag
    assert ("whole-module" in result["gpu_energy_counter"]["semantics"]) == (scope == "module")


def test_no_counter_gives_windows_with_time_and_the_tag_none(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)
    monkeypatch.setattr(worker, "GpuEnergyReader", lambda device, scope=None: pt.GpuEnergyReader(
        device, nvml=FakeNvml(not_supported=True), which=lambda name: None, scope=scope))
    result = window_run(monkeypatch, tmp_path, worker_data, scope="board")
    assert result["gpu_energy_counter"]["source"] == "unavailable" and result["gpu_energy_counter"]["scope_tag"] == "none"
    for record in measured(result):
        block = record["energy_window"]
        assert block["gpu_energy"]["scope_tag"] == "none" and block["gpu_energy"]["window_delta_mj"] is None
        assert all(run["gpu_energy"]["scope_tag"] == "none" for run in block["runs"])
        assert pt.energy_window_per_run_j(record) is None and record["seconds"] > 0
    assert result["query_execution_status"] == "complete"


def test_scope_none_reads_no_counter_and_tags_none(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)

    def forbidden(*args, **kwargs):
        raise AssertionError("no GPU counter is read on a host declared --gpu-energy-scope none")
    monkeypatch.setattr(worker, "GpuEnergyReader", forbidden)
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 2, "--gpu-energy-scope", "none", "--energy-window-s", "0.02",
                        *NO_IDLE)
    assert result["gpu_energy_counter"] == {"source": "disabled", "scope": "none", "scope_tag": "none"}
    assert result["energy_scope"]["gpu_counter_scope_tag"] == "none"
    for record in measured(result):
        energy = record["energy_window"]["gpu_energy"]
        assert energy["scope_tag"] == "none" and energy["window_delta_mj"] is None
        assert all("gpu_energy" not in run for run in record["energy_window"]["runs"])


def test_a_window_of_one_run_when_the_first_run_reaches_the_target(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, target="0.0000001")
    for record in measured(result):
        block = record["energy_window"]
        assert block["n"] == 1 and len(block["runs"]) == 1 and block["below_target"] is False and block["stop_reason"] == "target_reached"
        assert record["seconds"] == block["runs"][0]["seconds"]
        assert block["gpu_energy"]["window_delta_mj"] == block["gpu_energy"]["per_run_delta_mj"] == 2500


def test_the_run_count_is_capped_at_fifty(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=1, target="1000")
    assert {r["energy_window"]["n"] for r in measured(result)} == {50}
    assert all(len(r["energy_window"]["runs"]) == 50 for r in measured(result))
    assert all(r["energy_window"]["stop_reason"] == "max_runs" and r["energy_window"]["below_target"] for r in measured(result))


@pytest.mark.parametrize("script, target, n, reason", [
    ([0.5, 0.2, 0.2, 0.2, 0.2, 0.2], "0.7", 4, "target_reached"),        # 0.2 x 4 = 0.8 >= 0.7 > 0.6; the warm-up (0.5 s) does not count
    ([0.1, 5.0], "3", 1, "target_reached"),                              # the first run alone reaches the target
    ([100.0, 0.2], "0.7", 4, "target_reached"),                          # a huge warm-up changes nothing: t_hat is QA only
    ([0.0005, 0.4], "0.7", 2, "target_reached"),                         # a tiny warm-up neither
    ([0.001, 0.001], "1", 50, "max_runs"),                               # 50 runs of a millisecond: capped, below the target
    ([0.3, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1], "0.65", 7, "target_reached")])
def test_n_follows_the_elapsed_time_of_the_runs_not_the_warmup(monkeypatch, tmp_path, worker_data, scoped, script, target, n, reason):
    install_fake_suite(monkeypatch)
    result = scripted(monkeypatch, tmp_path, worker_data, script, target)
    for record in measured(result):
        block = record["energy_window"]
        assert block["n"] == n == len(block["runs"]) and block["stop_reason"] == reason
        assert block["t_hat_seconds"] == pytest.approx(script[0]) and block["target_seconds"] == float(target)
        assert block["window_seconds"] == pytest.approx(sum(r["seconds"] for r in block["runs"]))
        assert block["below_target"] == (reason == "max_runs")
        assert pt.energy_window_stop_violation([r["seconds"] for r in block["runs"]], float(target)) is None
        assert record["seconds"] == pytest.approx(block["window_seconds"] / n)
    assert not preflight.energy_window_problems(result, result["records"])


def test_trials_of_one_query_may_differ_in_n(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    ScriptedClock(monkeypatch, [0.1, 0.3, 0.3, 0.3, 0.9, 0.9])               # warm-up; trial 1 needs 3 runs of 0.3; trial 2 one of 0.9
    result = window_run(monkeypatch, tmp_path, worker_data, runs=2, target="0.8", extra=("--queries", "qa"))
    assert [r["energy_window"]["n"] for r in measured(result)] == [3, 1]
    assert not preflight.energy_window_problems(result, result["records"])


def test_a_spark_cpu_window_runs_n_times_with_the_first_runs_plan(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    install_fake_spark(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, engine="spark_cpu", runs=1, target="5")
    for record in measured(result):
        block = record["energy_window"]
        assert block["n"] > 1 and record["gpu_execution_class"] == "cpu_engine" and record["cpu_core_operators"]
        assert all("gpu_execution_class" not in run for run in block["runs"])           # only spark_gpu keeps a class per run
    assert sorted(p.name for p in (tmp_path / "out-spark_cpu" / "plans").iterdir()) == sorted(
        f"{q}-{ph}-{t}.txt" for q in ("qa", "qb") for ph, t in (("warmup", 0), ("measured", 1)))


GPU_PLAN = "== Final Plan ==\nGpuHashAggregate\nGpuFileGpuScan parquet\n"
CPU_PLAN = "== Final Plan ==\nHashAggregate\nFileScan parquet\n"


def install_gpu_spark(monkeypatch, plans):
    """A fake spark_gpu session whose k-th executed plan is plans[k] (then GPU_PLAN)."""
    consumed = []

    class Frame(FakeSparkFrame):
        @property
        def _jdf(self):
            plan = plans[len(consumed)] if len(consumed) < len(plans) else GPU_PLAN
            consumed.append(plan)
            return types.SimpleNamespace(queryExecution=lambda: types.SimpleNamespace(
                executedPlan=lambda: types.SimpleNamespace(toString=lambda: plan)))

    class Session:
        version = "3.5.9"
        sparkContext = types.SimpleNamespace(
            setLogLevel=lambda level: None, defaultParallelism=4,
            getConf=lambda: types.SimpleNamespace(getAll=lambda: [("spark.plugins", "com.nvidia.spark.SQLPlugin")],
                                                  get=lambda key, default="": default))
        read = types.SimpleNamespace(parquet=lambda path: Frame())

        def sql(self, text):
            return Frame()

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
    monkeypatch.setattr(worker, "gpu_inventory", lambda device: {})
    monkeypatch.setattr(worker, "make_gpu_probe", lambda device: None)
    return consumed


def test_spark_gpu_windows_keep_every_runs_plan_class(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    install_gpu_spark(monkeypatch, [])
    result = window_run(monkeypatch, tmp_path, worker_data, engine="spark_gpu", runs=1, target="5")
    for record in measured(result):
        runs = record["energy_window"]["runs"]
        assert len(runs) > 1 and {r["gpu_execution_class"] for r in runs} == {"gpu_operators_present"}
        assert all(r["gpu_operators"] == ["GpuFileGpuScan", "GpuHashAggregate"] for r in runs)
        assert record["peak_gpu_memory_bytes"] is None and all(r["peak_gpu_memory_bytes"] is None for r in runs)
        assert record["gpu_execution_class"] == "gpu_operators_present"
    assert not preflight.energy_window_problems(result, result["records"])


def test_a_spark_gpu_run_that_loses_its_gpu_plan_inside_a_window_stops_the_session(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    install_gpu_spark(monkeypatch, [GPU_PLAN, GPU_PLAN, GPU_PLAN, GPU_PLAN, CPU_PLAN])    # two warmups, run 1, run 2, run 3 on CPU
    with pytest.raises(RuntimeError, match="Missing GPU scan/aggregate"):
        window_run(monkeypatch, tmp_path, worker_data, engine="spark_gpu", runs=1, target="5")
    saved = json.loads((tmp_path / "out-spark_gpu" / "results.json").read_text())
    assert not [r for r in saved["records"] if r["phase"] == "measured"]                   # the window was never published
    assert saved["failure"]["type"] == "RuntimeError"


def test_the_later_runs_of_a_window_are_checked_against_the_first(monkeypatch, tmp_path, worker_data, scoped):
    import polars as pl
    install_fake_suite(monkeypatch)
    seen = {"qa": 0, "qb": 0}

    def drifting(query, tables):
        seen[query] += 1                    # call 1 is the warmup, call 2 run 1 of the only trial, call 4 run 3, call 5 run 4
        value = {4: 1.0 + 1e-12, 5: 2.0}.get(seen[query], 1.0) if query == "qa" else 1.0
        return pl.LazyFrame({"k": [1], query: [value]})
    monkeypatch.setattr(sys.modules["workloads"], "polars_query", drifting)
    monkeypatch.setattr(worker, "energy_window_complete", lambda seconds, target, max_runs=50: len(seconds) >= 4)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=1, target="5")
    qa = next(r for r in measured(result) if r["query"] == "qa")["energy_window"]["runs"]
    qb = next(r for r in measured(result) if r["query"] == "qb")["energy_window"]["runs"]
    assert [r["output_check"].get("agrees") for r in qa] == [None, True, True, False]      # 1e-12 is inside rel 1e-8, 2.0 is not
    assert [r["output_check"].get("agrees") for r in qb] == [None, True, True, True]
    assert qa[0]["output_check"]["checked_against"].startswith("polars reference")
    assert all(r["output_check"]["checked_against"] == "window run 1" for r in qa[1:])
    assert all(r["output_check"]["rows"] == 1 for r in qa + qb)
    assert all("rows" not in r for r in qb) and all("rows" not in r for r in qa[:3])        # agreeing runs store a verdict only
    bad = qa[3]
    assert bad["output_check"]["mismatch_count"] == 1 and bad["output_check"]["first_mismatch"]["reason"] == "numeric value differs"
    assert bad["rows"] == [{"k": 1, "qa": 2.0}]                                            # the evidence of the disagreement is kept
    flagged = preflight.energy_window_problems(dict(result, engine="polars"), result["records"])
    assert preflight.G2 in flagged.gates() and any("qa/measured1 run 4" in text for text in flagged.texts())
    assert preflight.G1 in flagged.gates()     # n was forced to 4 here, which the gate sees: 4 ms of runs is neither the target nor the cap


def test_a_failing_run_voids_the_window_and_the_query_is_not_run_again(monkeypatch, tmp_path, worker_data, scoped):
    import polars as pl
    suite = types.ModuleType("complex_workloads")
    install_fake_suite(monkeypatch)
    base = sys.modules["workloads"]
    for name in ("QUERY_IDS", "DESCRIPTIONS", "REQUIRED_TABLES", "FLOAT_COLUMNS"):
        setattr(suite, name, getattr(base, name))
    state = {"qa": 0}

    def flaky(query, tables):
        if query == "qa":
            state["qa"] += 1
            if state["qa"] == 4:                              # warmup is call 1: the third run of the first window
                raise RuntimeError("out of memory")
        return base.polars_query(query, tables)
    suite.polars_query, suite.spark_sql = flaky, base.spark_sql
    monkeypatch.setitem(sys.modules, "complex_workloads", suite)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=2, target="10", extra=("--suite", "complex"))
    assert [e["query"] for e in result["query_errors"]] == ["qa"]
    error = result["query_errors"][0]
    assert error["phase"] == "measured" and error["trial"] == 1 and error["window_run"] == 3 and "out of memory" in error["message"]
    assert [r["query"] for r in measured(result)] == ["qb", "qb"]                       # no record for the voided window, qa not retried
    assert result["query_execution_status"] == "partial" and state["qa"] == 4


class FakeBarrier:
    def __init__(self, engine, spark, owned, timeout_seconds):
        pass

    def cleanup(self):
        return {"engine": "polars", "status": "passed", "seconds": 0.0, "before": {"shuffle_files": 1, "shuffle_bytes": 10},
                "after": {"shuffle_files": 0, "shuffle_bytes": 0}}


def test_cleanup_runs_after_every_run_and_pairs_one_to_one_with_the_records(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    monkeypatch.setattr(worker, "CleanupBarrier", FakeBarrier)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=2, target="5", extra=("--query-cleanup", "verified_gc"))
    records = result["records"]
    cleanup = result["cleanup_records"]
    assert sorted((c["query"], c["phase"], c["trial"]) for c in cleanup) == sorted((r["query"], r["phase"], r["trial"]) for r in records)
    interim = result["window_cleanup_records"]
    runs_total = sum(r["energy_window"]["n"] for r in measured(result))
    assert len(interim) == runs_total - len(measured(result))                         # a barrier after runs 1..n-1 of every window
    for record in measured(result):
        n = record["energy_window"]["n"]
        mine = [c for c in interim if (c["query"], c["trial"]) == (record["query"], record["trial"])]
        assert sorted(c["window_run"] for c in mine) == list(range(1, n))
        last = next(c for c in cleanup if (c["query"], c["phase"], c["trial"]) == (record["query"], "measured", record["trial"]))
        assert last["window_run"] == n
    assert all(c["status"] == "passed" for c in cleanup + interim)
    # the campaign gate (preflight G5) counts the interim barriers too
    assert not preflight.energy_window_problems(result, records)


def test_the_gate_catches_a_window_that_broke_the_rule(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=1)
    good = preflight.energy_window_problems(result, result["records"])
    assert not good
    first = next(r for r in result["records"] if r["phase"] == "measured")
    for mutate, gate in ((lambda b: b.update(n=b["n"] + 1), "G1"),
                         (lambda b: b["runs"].pop(), "G1"),                       # 49 runs of a millisecond: neither the target nor the cap
                         (lambda b: b.update(target_seconds=b["target_seconds"] * 2), "G1"),
                         (lambda b: b.update(window_seconds=b["window_seconds"] * 2), "G1"),
                         (lambda b: b.update(t_hat_seconds=b["t_hat_seconds"] * 2), "G1"),
                         (lambda b: b["runs"][1]["output_check"].update(agrees=False, mismatch_count=3), "G2"),
                         (lambda b: b["gpu_energy"].pop("scope_tag"), "G1")):
        broken = json.loads(json.dumps(result))
        target = next(r for r in broken["records"] if r["phase"] == "measured" and r["query"] == first["query"])
        mutate(target["energy_window"])
        assert any(g.startswith(gate) for g in preflight_gates(preflight.energy_window_problems(broken, broken["records"])))
    stripped = json.loads(json.dumps(result))
    stripped.pop("energy_window_rule")
    assert preflight_gates(preflight.energy_window_problems(stripped, stripped["records"]))


def preflight_gates(problems):
    return problems.gates()


@pytest.mark.parametrize("flags, message", [
    (["--energy-window-s", "10"], "needs an explicit --gpu-energy-scope"),
    (["--gpu-energy-scope", "board", "--gpu-energy", "off"], "needs the counter"),
    (["--gpu-energy-scope", "board", "--energy-window-s", "0"], "positive"),
    (["--gpu-energy-scope", "board", "--energy-window-s", "nan"], "positive"),
    (["--gpu-energy-scope", "gpu"], "invalid choice"),
])
def test_invalid_energy_options_are_rejected_before_anything_runs(monkeypatch, tmp_path, worker_data, capsys, flags, message):
    install_fake_suite(monkeypatch)
    with pytest.raises(SystemExit) as raised:
        run_worker(monkeypatch, tmp_path, worker_data, "polars", 1, *flags)
    assert raised.value.code == 2 and message in capsys.readouterr().err
    assert not (tmp_path / "out-polars").exists()


def test_a_gpu_engine_cannot_run_on_a_host_declared_to_have_no_gpu_counter(monkeypatch, tmp_path, worker_data, capsys):
    install_fake_suite(monkeypatch)
    with pytest.raises(SystemExit):
        run_worker(monkeypatch, tmp_path, worker_data, "spark_gpu", 1, "--gpu-energy-scope", "none")
    assert "GPU engine" in capsys.readouterr().err


# ---- the output comparison is the gate's ----------------------------------------------------------------------------

CASES = [
    ([], []), ([{"a": 1, "b": 2.5}], [{"a": 1, "b": 2.5}]), ([{"a": 1}], [{"a": 2}]), ([{"a": 1}], [{"a": 1.0}]),
    ([{"a": 600037902}], [{"a": 600037902.5}]), ([{"x": 1.0}], [{"x": 1.0 + 1e-9}]), ([{"x": 1.0}], [{"x": 1.0 + 1e-7}]),
    ([{"x": 0.0}], [{"x": 5e-7}]), ([{"x": 0.0}], [{"x": 5e-6}]), ([{"x": float("nan")}], [{"x": float("nan")}]),
    ([{"x": float("inf")}], [{"x": float("inf")}]), ([{"a": True}], [{"a": 1}]), ([{"a": True}], [{"a": True}]),
    ([{"a": "x"}], [{"a": "y"}]), ([{"a": None}], [{"a": None}]), ([{"a": 1}], [{"b": 1}]), ([{"a": 1}, {"a": 2}], [{"a": 1}]),
    ([{"a": 1.5, "s": "t", "d": "2026-01-01"}], [{"a": 1.5, "s": "t", "d": "2026-01-02"}]),
]


@pytest.mark.parametrize("expected, actual", CASES)
def test_the_workers_row_comparison_is_the_preflight_g2_rule(expected, actual):
    verdict = preflight.compare_values(expected, actual)
    passed, count, first = worker.rows_agree(expected, actual)
    assert (passed, count) == (verdict["passed"], verdict["mismatch_count"])
    assert (first is None) == passed
    assert (worker.ROW_REL_TOL, worker.ROW_ABS_TOL) == (preflight.REL_TOL, preflight.ABS_TOL)


# ---- the in-session idle windows (design section 2.2) ---------------------------------------------------------------

IDLE = ("--session-idle-seconds", "0.06", "--session-idle-settle-seconds", "0.03")


def test_the_design_defaults_of_the_idle_windows():
    assert (pt.SESSION_IDLE_SECONDS, pt.SESSION_IDLE_SETTLE_SECONDS) == (30.0, 60.0)


def test_two_idle_windows_bracket_the_session_with_the_engine_ready(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=1, target="0.0000001", extra=IDLE)
    before, after = result["idle_windows"]
    assert [w["label"] for w in result["idle_windows"]] == ["before_warmups", "after_last_trial"]
    assert result["session_idle_rule"]["enabled"] is True and result["session_idle_rule"]["window_seconds"] == 0.06
    assert result["session_idle_rule"]["settle_seconds"] == 0.03
    records = result["records"]
    assert before["end_monotonic_ns"] < records[0]["timed_start_monotonic_ns"]                # nothing ran before it but the set-up
    assert after["start_monotonic_ns"] > records[-1]["timed_end_monotonic_ns"]                 # nothing runs after it
    assert before["end_monotonic_ns"] < after["start_monotonic_ns"]
    for window in (before, after):
        settle = window["settle"]
        assert settle["requested_seconds"] == 0.03 and (settle["end_monotonic_ns"] - settle["start_monotonic_ns"]) / 1e9 >= 0.03
        assert settle["end_monotonic_ns"] <= window["start_monotonic_ns"]                       # the settle precedes the window
        assert window["requested_seconds"] == 0.06 and window["actual_seconds"] >= 0.06
        assert (window["end_monotonic_ns"] - window["start_monotonic_ns"]) / 1e9 == window["actual_seconds"]
        for key in ("start_utc", "end_utc"):
            assert len(window[key].split(".")[1]) == len("123456+00:00")                        # UTC with microseconds, as every stamp
        energy = window["gpu_energy"]
        assert energy["scope_tag"] == "S1-counter" and energy["delta_mj"] == 2500 and energy["unit"] == "mJ"
        assert energy["start_read_monotonic_ns"][0] >= settle["end_monotonic_ns"]               # read after the settle, outside the window
        assert energy["start_read_monotonic_ns"][1] <= window["start_monotonic_ns"]
        assert window["end_monotonic_ns"] <= energy["end_read_monotonic_ns"][0]
        assert window["energy_interval_seconds"] == pytest.approx(window["actual_seconds"], abs=0.01)
        assert window["mean_power_w"] == pytest.approx(2.5 / window["energy_interval_seconds"])
    assert result["active_query"] is None
    assert measured(result) and all("energy_window" in r for r in measured(result))             # the windows do not disturb the trials


def test_the_idle_windows_are_reported_by_a_session_without_the_window_rule_too(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 1, "--gpu-device", "0", "--gpu-energy-scope", "module", *IDLE)
    assert len(result["idle_windows"]) == 2 and "energy_window_rule" not in result
    assert {w["gpu_energy"]["scope_tag"] for w in result["idle_windows"]} == {"S1-module"}
    assert all("energy_window" not in r for r in result["records"])


def test_the_idle_windows_use_the_design_lengths_unless_a_flag_says_otherwise(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    monkeypatch.setattr(worker, "SESSION_IDLE_SECONDS", 0.04)                                    # stand-ins for the 30 s and 60 s defaults
    monkeypatch.setattr(worker, "SESSION_IDLE_SETTLE_SECONDS", 0.02)
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 1, "--gpu-energy-scope", "board")
    assert [(w["requested_seconds"], w["settle"]["requested_seconds"]) for w in result["idle_windows"]] == [(0.04, 0.02)] * 2


def test_zero_disables_the_idle_windows_and_paper1_has_none(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    off = run_worker(monkeypatch, tmp_path / "off", worker_data, "polars", 1, "--gpu-energy-scope", "board", "--session-idle-seconds", "0")
    assert off["idle_windows"] == [] and off["session_idle_rule"]["enabled"] is False
    plain = run_worker(monkeypatch, tmp_path / "plain", worker_data, "polars", 1)
    assert "idle_windows" not in plain and "session_idle_rule" not in plain


def test_idle_windows_on_a_host_without_a_gpu_counter_still_carry_stamps(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 1, "--gpu-energy-scope", "none", *IDLE)
    for window in result["idle_windows"]:
        assert window["gpu_energy"] == {"source": "disabled", "scope_tag": "none"}
        assert window["mean_power_w"] is None and window["energy_interval_seconds"] is None
        assert window["start_monotonic_ns"] < window["end_monotonic_ns"] and window["settle"]["start_monotonic_ns"]       # CPU energy can be cut


def test_an_unreadable_counter_gives_idle_windows_without_power(monkeypatch, tmp_path, worker_data):
    install_fake_suite(monkeypatch)
    monkeypatch.setattr(worker, "GpuEnergyReader", lambda device, scope=None: pt.GpuEnergyReader(
        device, nvml=FakeNvml(not_supported=True), which=lambda name: None, scope=scope))
    result = run_worker(monkeypatch, tmp_path, worker_data, "polars", 1, "--gpu-energy-scope", "board", *IDLE)
    for window in result["idle_windows"]:
        assert window["gpu_energy"]["scope_tag"] == "none" and window["gpu_energy"]["delta_mj"] is None and window["mean_power_w"] is None


@pytest.mark.parametrize("flags, message", [(["--session-idle-seconds", "5"], "needs an explicit --gpu-energy-scope"),
                                            (["--session-idle-settle-seconds", "5"], "needs an explicit --gpu-energy-scope"),
                                            (["--gpu-energy-scope", "board", "--session-idle-seconds", "-1"], "non-negative"),
                                            (["--gpu-energy-scope", "board", "--session-idle-settle-seconds", "nan"], "non-negative")])
def test_invalid_idle_options_are_rejected_before_anything_runs(monkeypatch, tmp_path, worker_data, capsys, flags, message):
    install_fake_suite(monkeypatch)
    with pytest.raises(SystemExit) as raised:
        run_worker(monkeypatch, tmp_path, worker_data, "polars", 1, *flags)
    assert raised.value.code == 2 and message in capsys.readouterr().err and not (tmp_path / "out-polars").exists()


def test_idle_window_power_from_the_counter_reads():
    energy = {"delta_mj": 90_000, "start_read_monotonic_ns": [1_000, 3_000], "end_read_monotonic_ns": [30_000_001_000, 30_000_003_000]}
    seconds, watts = pt.idle_window_power_w(energy)
    assert seconds == 30.0 and watts == 3.0                              # 90 J over the 30 s between the midpoints of the two reads
    for bad in (None, {}, {"delta_mj": None}, {"delta_mj": True, "start_read_monotonic_ns": [1, 2], "end_read_monotonic_ns": [3, 4]},
                {"delta_mj": -5, "start_read_monotonic_ns": [1, 2], "end_read_monotonic_ns": [3, 4]},
                {"delta_mj": 5, "start_read_monotonic_ns": [3, 4], "end_read_monotonic_ns": [1, 2]},
                {"delta_mj": 5, "start_read_monotonic_ns": [1, 2]}):
        assert pt.idle_window_power_w(bad) == (None, None)
