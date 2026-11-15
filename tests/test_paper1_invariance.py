"""With none of the Paper 3 energy flags, the harness writes the same records and files as it did for Paper 1.

tests/golden_paper1_shapes.json holds the shape (key names, nesting, value types; never the values) of what worker.py and
run_experiment.py wrote before the opt-in Paper 3 energy options existed, plus the exact telemetry and worker command
lines. The tests re-run the same fake sessions and require equality. Regenerate it only for a deliberate Paper 1 format
change: P3_WRITE_GOLDEN=1 python3 -m pytest tests/test_paper1_invariance.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import power_telemetry as pt  # noqa: E402
import run_campaign as rc  # noqa: E402
import worker  # noqa: E402
from test_power_telemetry import (  # noqa: E402,F401  (fixtures are imported by name so pytest registers them here)
    FakeEnergyReader, FakeNvml, FakePopen, hermetic_gpu_energy, host, install_fake_spark, install_fake_suite, layout,
    run_experiment, run_worker, worker_data)

GOLDEN = Path(__file__).with_name("golden_paper1_shapes.json")


PLATFORM_DEPENDENT = ("cpu_affinity", "affinity", "timezone", "source_sha256")     # shape varies with the OS or the files present


def shape(value):
    """Key names, nesting and value types of a JSON value; list elements are merged by their distinct shapes.

    The few values whose shape depends on the operating system or on which files sit beside the code are not compared."""
    if isinstance(value, dict):
        return {key: ("<platform dependent>" if key in PLATFORM_DEPENDENT else shape(value[key])) for key in sorted(value)}
    if isinstance(value, list):
        distinct = []
        for item in value:
            item_shape = shape(item)
            if item_shape not in distinct:
                distinct.append(item_shape)
        return distinct
    return type(value).__name__


def worker_shape(result):
    records = result["records"]
    return {"top": shape({k: v for k, v in result.items() if k not in ("records",)}),
            "warmup_record": shape(next(r for r in records if r["phase"] == "warmup")),
            "measured_record": shape(next(r for r in records if r["phase"] == "measured")),
            "record_order": [(r["query"], r["phase"], r["trial"]) for r in records]}


def flags_of(command):
    return [part for part in command if part.startswith("--")]


def capture_all(monkeypatch, tmp_path, worker_data, layout, host):
    out = {}
    install_fake_suite(monkeypatch)
    out["worker_polars_counter_unavailable"] = worker_shape(run_worker(monkeypatch, tmp_path / "a", worker_data, "polars", 2))
    monkeypatch.setattr(worker, "GpuEnergyReader", FakeEnergyReader)
    out["worker_polars_counter"] = worker_shape(
        run_worker(monkeypatch, tmp_path / "b", worker_data, "polars", 2, "--gpu-device", "0"))
    install_fake_spark(monkeypatch)
    out["worker_spark_cpu_counter"] = worker_shape(
        run_worker(monkeypatch, tmp_path / "c", worker_data, "spark_cpu", 2, "--gpu-device", "0"))
    out["energy_counter_description"] = pt.GpuEnergyReader(0, nvml=FakeNvml(), which=lambda name: None).describe()
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0",
                                           "--power-telemetry", "off")
    out["run_experiment_polars"] = {
        "environment": shape({k: v for k, v in environment.items() if k != "inventory"}),
        "inventory_commands": [["<python>" if part == sys.executable else part for part in item["command"]][:2]
                               if item["command"][0] == "df" else
                               ["<python>" if part == sys.executable else part for part in item["command"]]
                               for item in environment["inventory"]],
        "schedule": shape(schedule), "monitor_command_flags": FakePopen.telemetry[0][:1] + flags_of(FakePopen.telemetry[0]),
        "monitor_query": FakePopen.telemetry[0][1], "worker_flags": flags_of(FakePopen.launched[0]),
        "files": sorted(p.name for p in layout.out.iterdir())}
    return out


def test_paper1_shapes_are_unchanged(monkeypatch, tmp_path, worker_data, layout, host):
    live = capture_all(monkeypatch, tmp_path, worker_data, layout, host)
    live = json.loads(json.dumps(live))                                  # tuples to lists, as in the golden file
    if os.environ.get("P3_WRITE_GOLDEN") == "1":
        GOLDEN.write_text(json.dumps(live, indent=1, sort_keys=True) + "\n")
        pytest.skip("golden file written")
    golden = json.loads(GOLDEN.read_text())
    assert set(live) == set(golden)
    for key in golden:
        assert live[key] == golden[key], key


def test_the_default_campaign_command_line_has_no_paper3_flags():
    cfg = rc.resolve_config(rc.parse_args(["--dry-run"]))
    job = rc.build_units(cfg)[0].jobs[0]
    command = rc.job_command(job, Path("/tmp/out"), cfg)
    assert not [part for part in command if part in ("--energy-window-s", "--gpu-energy-scope")]
    assert "energy" not in rc.design_dict(cfg, rc.build_units(cfg))
