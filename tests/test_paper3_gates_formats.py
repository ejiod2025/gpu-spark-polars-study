"""The gates read what the harness really writes: run_experiment.py's schedule items and worker.py's results.json.

tests/paper3_fakes.py lays synthetic runs out by hand; this file runs the real runner (fake nvidia-smi) and the real worker (fake
counter) and checks that every key and layout paper3_gates.py relies on is there, so a change in the harness's records fails here
instead of silently turning a gate vacuous.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_gates as pg  # noqa: E402
import power_telemetry as pt  # noqa: E402
from paper3_fakes import host_gates  # noqa: E402
from test_energy_window import IDLE, scoped, window_run  # noqa: E402,F401
from test_paper3_runner import QUIET, smi  # noqa: E402,F401
from test_power_telemetry import (FakePopen, hermetic_gpu_energy, host, install_fake_suite, layout, run_experiment,  # noqa: E402,F401
                                  worker_data)


def test_the_runners_schedule_items_hold_what_g8_g11_and_g12_read(monkeypatch, layout, smi):
    schedule, environment = run_experiment(monkeypatch, layout, ["polars", "spark_gpu"], *QUIET, "--gpu-energy-scope", "board",
                                           "--session-cooldown-seconds", "0")
    run, sessions = pg.load_run(layout.out, "baseline", host_gates("h100"))
    assert sorted(s.engine for s in sessions) == ["polars", "spark_gpu"]       # the order is a seeded shuffle unless --engine-order
    for session in sessions:
        item = session.item
        assert session.complete
        for name in pg.RUNNER_STAMPS:
            assert pg.stamp_of(item[name]) is not None, name
        assert pg.chrony_problems(item) == []
        for key, what in ((pg.POWER_STATE_ITEM_KEY, "POWER"), (pg.PERFORMANCE_STATE_ITEM_KEY, "PERFORMANCE")):
            problems, texts = pg.state_text_problems(session, run, key, what)
            assert problems == [] and set(texts) == {"start", "end"}
        assert all(pg.parse_power_limit(text) == 700.0 for text in pg.state_text_problems(session, run, pg.POWER_STATE_ITEM_KEY, "POWER")[1].values())
        gate = item["gpu_idle_gate"]
        assert {"idle", "compute_apps", "error"} <= set(gate) and gate["idle"] is True
        assert {"compute_apps", "error"} <= set(item[pg.COMPUTE_APPS_END_KEY])
    assert environment["gpu_telemetry"]["monitor_tz_env"] == "UTC" and environment["gpu_telemetry"]["interval_ms"] == 200


def test_the_gpu_column_names_of_the_runners_query_are_the_ones_the_gates_look_for(monkeypatch, layout, smi):
    run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "board")
    command = next(c for c, _ in smi.popen if c[0] == "nvidia-smi")
    queried = command[1].removeprefix("--query-gpu=").split(",")
    assert "utilization.gpu" in queried and pg.TEMPERATURE_COLUMN in queried and any(c in queried for c in pg.THROTTLE_COLUMNS)
    header = ", ".join(f"{name} [x]" for name in queried)
    parsed_names = [__import__("re").sub(r"\s*\[[^\]]*\]\s*$", "", cell.strip()) for cell in header.split(",")]
    assert parsed_names == queried


def test_the_workers_results_hold_what_g6_g7_g9_and_g10_read(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=3, target="0.0001", extra=IDLE)
    root = tmp_path / "run"
    (root / "sf1" / "polars").mkdir(parents=True)
    (root / "sf1" / "polars" / "results.json").write_text(json.dumps(result))
    item = {"scale": "1", "engine": "polars", "status": "complete", "returncode": 0}
    session = pg.Session("baseline", "1", "polars", item, root / "sf1" / "polars", result)
    assert [w["label"] for w in result[pg.IDLE_BLOCK]] == list(pg.IDLE_LABELS)
    assert set(result[pg.SESSION_COUNTERS]) == set(pg.SESSION_COUNTER_LABELS)
    for label in pg.SESSION_COUNTER_LABELS:
        entry = result[pg.SESSION_COUNTERS][label]
        assert pg.stamp_of(entry) is not None
        read = entry["gpu_energy_read"]
        assert pg.is_int(read["mj"]) and len(read["monotonic_ns"]) == 2 and read["source"] and read["scope_tag"] == "S1-counter"
    reads, unusable = pg.counter_reads(result)
    assert unusable == 0 and len(reads) >= 2 * 2 + 2 + 6 * 2, "idle windows, session counters and the runs of the trials"
    assert sorted(reads) == reads or [r[1] for r in sorted(reads)] == sorted(r[1] for r in reads), "the fake counter only moves forward"
    windows = pg.session_windows(session)
    assert {w["kind"] for w in windows} == {"measured", "idle"} and sum(w["kind"] == "idle" for w in windows) == 2
    measured = [r for r in result["records"] if r["phase"] == "measured"]
    assert len(measured) == 6 and len([w for w in windows if w["kind"] == "measured"]) == 6, "two fake queries, three trials each"
    for record in measured:
        block = record["energy_window"]
        assert block["target_seconds"] == 0.0001 and block["stop_reason"] in ("target_reached", "max_runs")
        assert pt.energy_window_stop_violation([r["seconds"] for r in block["runs"]], block["target_seconds"]) is None
        assert pg.window_rule_violation(block["runs"], target=block["target_seconds"]) is None
        assert block["n"] == len(block["runs"]) and pg.is_number(block["per_run_seconds"]) and pg.is_number(block["window_seconds"])
        assert pg.epoch(block["span"]["start_utc"]) is not None and pg.epoch(block["span"]["end_utc"]) > pg.epoch(block["span"]["start_utc"])
    for window in result[pg.IDLE_BLOCK]:
        assert pg.counter_idle_power(window["gpu_energy"]) == pytest.approx(window["mean_power_w"]), "recomputed idle power equals the worker's"


def test_the_gates_recompute_the_workers_idle_power_the_same_way_for_a_module_scope_host(monkeypatch, tmp_path, worker_data, scoped):
    install_fake_suite(monkeypatch)
    result = window_run(monkeypatch, tmp_path, worker_data, runs=1, scope="module", target="0.0001", extra=IDLE)
    assert {w["gpu_energy"]["scope_tag"] for w in result[pg.IDLE_BLOCK]} == {"S1-module"}
    assert pg.counter_idle_power(result[pg.IDLE_BLOCK][0]["gpu_energy"]) == pytest.approx(result[pg.IDLE_BLOCK][0]["mean_power_w"])
