"""Without --paper3-host, run_campaign.py resolves, plans and launches exactly what it did before Paper 3 campaign mode existed.

tests/golden_paper1_campaign.json was captured from the run_campaign.py of commit 86f3b15 (before the Paper 3 mode): the resolved
design, every job command, every gate command and the printed dry-run schedule for several argument sets. These tests recompute
them with the current code and require equality. Regenerate only for a deliberate Paper 1 change:
P3_WRITE_GOLDEN=1 python3 -m pytest tests/test_paper1_campaign_invariance.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import run_campaign as rc  # noqa: E402

GOLDEN = Path(__file__).with_name("golden_paper1_campaign.json")
COMMON = ["--root", "/r", "--data-root", "/d", "--jar", "/j.jar", "--a100compat-env", "/env"]
P1_CASES = {
    "default": [],
    "tiny": ["--rounds", "2", "--scales-override", "1", "--engines-override", "polars", "spark_cpu", "--cpus-override", "4",
             "--driver-memory-override", "4g", "--no-lease-check"],
    "paper3_energy_flags": ["--gpu-energy-scope", "board", "--energy-window-s", "10", "--cpu-energy", "require",
                            "--idle-baseline-seconds", "0", "--redact-gpu-identifiers", "off"],
    "pinned": ["--socket", "0", "--no-membind", "--engines-override", "polars", "spark_cpu"],
    "other_host": ["--allow-any-gpu", "--host-label", "gpu_1x_b200_sxm6", "--stop-after-round", "3"],
    "cost": ["--cost-provider", "lambda-labs", "--cost-hourly-price-usd", "4.29", "--cost-region", "us-west-1",
             "--cost-price-snapshot-utc", "2026-11-11T14:00:00Z", "--host-label", "gpu_1x_h100_sxm5"],
}


def normalise(value):
    return json.loads(json.dumps(value, default=str).replace(sys.executable, "<python>").replace(str(REPO), "<repo>"))


def capture(module, argv):
    """Everything a campaign decides before it launches anything, as JSON-able data."""
    cfg = module.resolve_config(module.parse_args([*COMMON, *argv]))
    units = module.build_units(cfg)
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        module.print_schedule(cfg, units, module.design_dict(cfg, units), verbose=True)
    main_units = [u for u in units if u.kind == "main"]
    return normalise({
        "design": module.design_dict(cfg, units),
        "jobs": [[u.uid, j.key, module.job_command(j, u.base / u.name / j.out_rel, cfg)] for u in units for j in u.jobs],
        "gates": [module.gate_command(u, u.base / u.name, cfg, u.base / u.name / "preflight", prior_reports=[Path("/prior.json")])
                  for u in main_units],
        "cost": cfg.cost, "telemetry": cfg.telemetry, "formal": cfg.formal, "test_flags": cfg.test_flags,
        "allow_any_gpu": cfg.allow_any_gpu, "engines": cfg.engines, "scales": cfg.scales,
        "critical_sources": list(module.CRITICAL_SOURCES), "telemetry_flags": module.TELEMETRY_FLAGS,
        "printed": printed.getvalue(),
    })


@pytest.mark.parametrize("name", sorted(P1_CASES))
def test_a_paper1_campaign_is_planned_exactly_as_before(name):
    live = capture(rc, P1_CASES[name])
    if os.environ.get("P3_WRITE_GOLDEN") == "1":
        golden = json.loads(GOLDEN.read_text()) if GOLDEN.exists() else {}
        golden[name] = live
        GOLDEN.write_text(json.dumps(golden, indent=1, sort_keys=True) + "\n")
        pytest.skip("golden file written")
    golden = json.loads(GOLDEN.read_text())[name]
    assert live == golden


def test_the_golden_cases_cover_the_documented_set():
    assert set(json.loads(GOLDEN.read_text())) == set(P1_CASES)


def test_a_paper1_run_has_no_paper3_state():
    cfg = rc.resolve_config(rc.parse_args(COMMON))
    assert cfg.paper3 is None
    units = rc.build_units(cfg)
    design = rc.design_dict(cfg, units)
    assert "paper3" not in design and design["seed"] == 20261029 and design["design_doc"] == "docs/H100_EXPERIMENT_DESIGN.md"
    assert [u.round for u in units if u.kind == "main"] == [1, 2, 3, 4] and any(u.kind == "side" for u in units)
    assert "--session-idle-seconds" not in " ".join(rc.job_command(units[0].jobs[0], Path("/o"), cfg))
    assert rc.design_doc_hashes(cfg) == {"design_doc_sha256": rc.sha256_file(rc.DESIGN_DOC)}


def test_the_paper3_flags_are_refused_without_the_paper3_host(capsys):
    for flags in (["--paper3-launch", "1"], ["--paper3-lock-date", "20261116"], ["--cost-instance-id", "abc"],
                  ["--stop-after-round", "5"]):
        with pytest.raises(SystemExit) as raised:
            rc.parse_args(flags)
        assert raised.value.code == 2
    assert "--paper3-host" in capsys.readouterr().err
