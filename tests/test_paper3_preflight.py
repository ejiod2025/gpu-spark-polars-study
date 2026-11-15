"""preflight.py --paper3: the strictness of --formal for a Paper 3 host's round, with its own engine order, rounds 5-8, a two-engine
host, and the hook that runs the Paper 3 gates G6-G12 (paper3_gates.py).

The rounds are built by tests/test_campaign_gates.py (the pristine formal round of Paper 1); only the flags differ. Paper 1's own
behaviour (--formal, rounds 1-4) is pinned in test_campaign_gates.py and must not change.
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
import preflight  # noqa: E402
from paper3_fakes import HOST_ENTRIES, RunBuilder, edit_results, host_gates  # noqa: E402
from test_campaign_gates import (BASELINE_SCALES, COMPLEX_SCALES, DECLARED, ENGINES, GO, ROUND_ORDER, build_round, edit,  # noqa: E402
                                 edit_schedule, gate_verdicts)

PAPER3 = ["--paper3", "--skip-run", "--skip-env", "--skip-inputs", "--runs", "3", "--expect-gpu-unsupported", *DECLARED, "--expect-cpus", "26"]


def run_p3(root, tmp_path, *extra, round_number=5, order=ROUND_ORDER[1], first="baseline", engines=ENGINES, name="p3", suites=("baseline", "complex"),
           scales=True):
    report = tmp_path / f"{name}.json"
    argv = [*PAPER3, "--round", str(round_number), "--engines", *engines, "--expect-engine-order", *order,
            "--baseline-dir", str(root / "baseline"), "--complex-dir", str(root / "complex"),
            "--output", str(tmp_path / f"{name}-out"), "--report", str(report), "--suites", *suites]
    if first:
        argv += ["--expect-suite-first", first]
    if scales:
        argv += ["--baseline-scales", *BASELINE_SCALES, "--complex-scales", *COMPLEX_SCALES]
    code = preflight.main([*argv, *extra])
    return code, json.loads(report.read_text())


def usage_error(argv):
    with pytest.raises(SystemExit) as stop:
        preflight.main(argv)
    return stop.value.code


# ---- a Paper 3 round ----------------------------------------------------------------------------------------------------------

def test_a_round_of_the_second_square_validates_with_its_own_engine_order(tmp_path):
    root = build_round(tmp_path / "r", 1)
    code, report = run_p3(root, tmp_path)
    assert code == 0, report["failing_checks"][:4]
    assert report["verdict"] == "pass" and report["expected"]["round"] == 5 and report["expected"]["round_engine_order"] == ROUND_ORDER[1]
    assert report["expected"]["paper3"] == {"suite_first": "baseline", "host_gates": None} and report["expected"]["formal"] is False
    assert len(report["sessions"]) == 32 and report["expected"]["runs"] == 3


@pytest.mark.parametrize("number", [5, 6, 7, 8])
def test_every_round_of_the_second_square_is_accepted_as_a_round_number(tmp_path, number):
    root = build_round(tmp_path / "r", 4)
    code, report = run_p3(root, tmp_path, round_number=number, order=ROUND_ORDER[4], first="complex", name=f"r{number}")
    assert code == 0 and report["expected"]["round"] == number


def test_a_round_run_in_another_order_than_the_one_stated_fails(tmp_path):
    root = build_round(tmp_path / "r", 2)
    code, report = run_p3(root, tmp_path, order=ROUND_ORDER[1], first="complex")
    assert code == 1 and report["gates"][GO]["verdict"] == "fail"
    assert any(c["id"] == "baseline:executed_order" and c["status"] == "FAIL" for c in report["checks"])


def test_the_first_suite_is_the_one_stated_not_the_parity_of_the_round_number(tmp_path):
    root = build_round(tmp_path / "r", 1)                              # baseline first
    code, _ = run_p3(root, tmp_path, round_number=6, first="baseline", name="ok")
    assert code == 0, "round 6 is even, Paper 1's rule would want complex first; this round states baseline"
    code, report = run_p3(root, tmp_path, round_number=5, first="complex", name="bad")
    assert code == 1 and any(c["id"] == "suite_order" and c["status"] == "FAIL" for c in report["checks"])


def test_a_two_engine_host_validates_without_formal(tmp_path):
    root = build_round(tmp_path / "r", 2, engines=("polars", "spark_cpu"))
    order = [e for e in ROUND_ORDER[2] if e in ("polars", "spark_cpu")]
    code, report = run_p3(root, tmp_path, round_number=2, order=order, first="complex", engines=("polars", "spark_cpu"))
    assert code == 0 and report["expected"]["engines"] == ["polars", "spark_cpu"] and len(report["sessions"]) == 16


def test_a_baseline_only_host_with_other_scales_validates(tmp_path):
    scales = {"baseline": ["10", "30", "300"], "complex": []}
    root = build_round(tmp_path / "r", 1, scales=scales, engines=("polars", "spark_cpu"))
    order = [e for e in ROUND_ORDER[1] if e in ("polars", "spark_cpu")]
    report_path = tmp_path / "vm.json"
    code = preflight.main([*PAPER3, "--round", "1", "--engines", "polars", "spark_cpu", "--expect-engine-order", *order, "--suites", "baseline",
                           "--baseline-scales", "10", "30", "300", "--baseline-dir", str(root / "baseline"), "--output", str(tmp_path / "vm-out"),
                           "--report", str(report_path)])
    report = json.loads(report_path.read_text())
    assert code == 0 and report["suites"] == ["baseline"] and report["expected"]["suites"]["baseline"]["scales"] == ["10", "30", "300"]


# ---- --paper3 is as strict as --formal about the records -----------------------------------------------------------------------

def test_paper3_judges_the_records_with_the_formal_rules_where_the_explicit_flags_do_not(tmp_path):
    root = build_round(tmp_path / "r", 1)
    edit_schedule(root, "baseline", lambda schedule: schedule[0].pop("gpu_idle_gate"))
    code, report = run_p3(root, tmp_path, name="strict")
    assert code == 1 and any(c["status"] == "FAIL" and "idle" in c["detail"].lower() for c in report["checks"]), report["failing_checks"][:3]
    lax = tmp_path / "lax.json"
    code = preflight.main(["--skip-run", "--skip-env", "--skip-inputs", "--round", "1", "--runs", "3", "--engines", *ENGINES, "--error-policy", "classified",
                           "--expect-gpu-unsupported", *DECLARED, "--expect-cpus", "26", "--baseline-scales", *BASELINE_SCALES,
                           "--complex-scales", *COMPLEX_SCALES, "--baseline-dir", str(root / "baseline"), "--complex-dir", str(root / "complex"),
                           "--output", str(tmp_path / "lax-out"), "--report", str(lax)])
    assert code == 0, "without --formal or --paper3 the same missing gate record passes: the strictness is what --paper3 adds"


def test_paper3_requires_polars_as_the_reference_engine(tmp_path):
    root = build_round(tmp_path / "r", 1, engines=("spark_cpu", "spark_gpu"))
    order = [e for e in ROUND_ORDER[1] if e in ("spark_cpu", "spark_gpu")]
    assert usage_error([*PAPER3, "--round", "1", "--engines", "spark_cpu", "spark_gpu", "--expect-engine-order", *order, "--expect-suite-first", "baseline",
                        "--baseline-dir", str(root / "baseline"), "--complex-dir", str(root / "complex"), "--output", str(tmp_path / "o"),
                        "--report", str(tmp_path / "r.json")]) == 2


# ---- usage errors ---------------------------------------------------------------------------------------------------------------

def test_rounds_5_to_8_are_paper3_only(tmp_path, capsys):
    root = build_round(tmp_path / "r", 1)
    base = ["--skip-run", "--skip-env", "--skip-inputs", "--baseline-dir", str(root / "baseline"), "--complex-dir", str(root / "complex"),
            "--output", str(tmp_path / "o"), "--report", str(tmp_path / "r.json")]
    assert usage_error(["--formal", "--round", "5", *base]) == 2
    assert "rounds 5-8 are Paper 3's: add --paper3" in capsys.readouterr().err
    assert usage_error(["--round", "9", *base]) == 2
    assert usage_error(["--round", "5", *base]) == 2


@pytest.mark.parametrize("mutate,message", [
    (lambda a: a.remove("--skip-run"), "--paper3 validates a finished round: add --skip-run"),
    (lambda a: a.__setitem__(slice(a.index("--round"), a.index("--round") + 2), []), "--paper3 requires --round"),
    (lambda a: a.__setitem__(slice(a.index("--runs"), a.index("--runs") + 2), []), "--paper3 requires --runs"),
    (lambda a: a.__setitem__(slice(a.index("--expect-suite-first"), a.index("--expect-suite-first") + 2), []), "requires --expect-suite-first"),
    (lambda a: a.extend(["--formal"]), "alternatives"),
    (lambda a: a.extend(["--error-policy", "strict"]), "classified"),
])
def test_paper3_usage_errors(tmp_path, capsys, mutate, message):
    root = build_round(tmp_path / "r", 1)
    argv = [*PAPER3, "--round", "5", "--engines", *ENGINES, "--expect-engine-order", *ROUND_ORDER[1], "--expect-suite-first", "baseline",
            "--baseline-dir", str(root / "baseline"), "--complex-dir", str(root / "complex"), "--output", str(tmp_path / "o"), "--report", str(tmp_path / "r.json")]
    mutate(argv)
    assert usage_error(argv) == 2
    assert message in capsys.readouterr().err


def test_the_order_must_be_a_permutation_of_the_engines_and_belongs_to_paper3(tmp_path, capsys):
    root = build_round(tmp_path / "r", 1)
    common = ["--skip-run", "--skip-env", "--skip-inputs", "--baseline-dir", str(root / "baseline"), "--complex-dir", str(root / "complex"),
              "--output", str(tmp_path / "o"), "--report", str(tmp_path / "r.json")]
    assert usage_error([*PAPER3, "--round", "5", "--engines", *ENGINES, "--expect-engine-order", "polars", "spark_cpu", "--expect-suite-first", "baseline", *common[3:]]) == 2
    assert "permutation" in capsys.readouterr().err
    assert usage_error([*PAPER3, "--round", "5", "--engines", "polars", "spark_cpu", "--expect-engine-order", "polars", "polars", "--expect-suite-first", "baseline", *common[3:]]) == 2
    assert usage_error(["--formal", "--skip-run", "--skip-env", "--skip-inputs", "--round", "1", "--expect-engine-order", *ROUND_ORDER[1], *common[3:]]) == 2
    assert "belongs to --paper3" in capsys.readouterr().err


def test_a_formal_report_has_no_paper3_keys(tmp_path):
    from test_campaign_gates import run_preflight
    root = build_round(tmp_path / "r", 1)
    _, report = run_preflight(root, tmp_path)
    assert "paper3" not in report["expected"] and report["expected"]["round_engine_order"] == ROUND_ORDER[1]


# ---- the hook: G6-G12 ---------------------------------------------------------------------------------------------------------------

def config_file(tmp_path, **entries):
    path = tmp_path / "hosts.json"
    hosts = {name: json.loads(json.dumps(HOST_ENTRIES[name])) for name in ("h100", "c-vm")}
    for name, change in entries.items():
        hosts[name].update(change)
    path.write_text(json.dumps({"format": 1, "hosts": hosts}))
    return path


def hook_args(tmp_path, captures=()):
    out = tmp_path / "hook-out"
    out.mkdir(exist_ok=True)
    return type("Args", (), {"output": out, "host_capture": list(captures)})()


def clean_capture_files(tmp_path):
    paths = []
    for label in ("start", "end"):
        path = tmp_path / f"{label}.json"
        path.write_text(json.dumps({"label": label, "gpu_compute_apps": {"returncode": 0, "apps": []}, "scheduled": {}}))
        paths.append(path)
    return paths


def test_the_hook_adds_g6_to_g12_as_checks_and_writes_the_report(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars", "polars_gpu")).build()
    checks = preflight.Checks(quiet=True)
    preflight.paper3_gate_checks(checks, hook_args(tmp_path, clean_capture_files(tmp_path)), host_gates("h100"), ["baseline"], {"baseline": run.root})
    by_id = {c["id"]: c for c in checks.items}
    assert set(by_id) == {f"paper3:G{n}" for n in range(6, 13)} and checks.failed() is False
    assert by_id["paper3:G6"]["status"] == by_id["paper3:G12"]["status"] == "PASS"
    assert all(by_id[f"paper3:G{n}"]["status"] == "INFO" for n in (7, 8, 9, 10, 11))
    assert json.loads((tmp_path / "hook-out" / "paper3-gates.json").read_text())["round_failed"] is False


def test_a_g6_or_g12_failure_is_a_failing_check_and_flag_gates_are_not(tmp_path):
    run = RunBuilder(tmp_path / "run", host="h100", engines=("polars", "polars_gpu"), post_idle_scale=1.5).build()
    checks = preflight.Checks(quiet=True)
    preflight.paper3_gate_checks(checks, hook_args(tmp_path, clean_capture_files(tmp_path)), host_gates("h100"), ["baseline"], {"baseline": run.root})
    assert checks.failed() is False, "G10 is an eligibility flag"
    assert next(c for c in checks.items if c["id"] == "paper3:G10")["detail"].startswith("0 pass, 2 flagged")
    edit_results(run.root, "1", "polars_gpu", lambda r: r[pg.COUNTER_BLOCK].update(source="x"))
    checks = preflight.Checks(quiet=True)
    preflight.paper3_gate_checks(checks, hook_args(tmp_path, clean_capture_files(tmp_path)), host_gates("h100"), ["baseline"], {"baseline": run.root})
    failing = [c for c in checks.items if c["status"] == "FAIL"]
    assert checks.failed() is True and [c["id"] for c in failing] == ["paper3:G6"] and "GPU counter source 'x'" in failing[0]["detail"]
    assert not any(c["id"] == "paper3:G6" and c["status"] == "PASS" for c in checks.items)


def test_a_run_that_cannot_be_read_is_a_failing_check(tmp_path):
    checks = preflight.Checks(quiet=True)
    preflight.paper3_gate_checks(checks, hook_args(tmp_path), host_gates("h100"), ["baseline"], {"baseline": tmp_path / "missing"})
    assert checks.failed() and checks.items[0]["id"] == "paper3_gates" and "schedule.json missing" in checks.items[0]["detail"]


def test_the_hook_through_main_on_a_round_whose_host_measures_no_energy(tmp_path):
    root = build_round(tmp_path / "r", 1)
    config = config_file(tmp_path, h100={"energy_measured": False, "t_min_s": "not_applicable"})
    code, report = run_p3(root, tmp_path, "--paper3-host", "h100", "--paper3-host-config", str(config), name="hook")
    assert code == 0, report["failing_checks"][:3]
    gates_report = json.loads((tmp_path / "hook-out" / "paper3-gates.json").read_text())
    assert gates_report["host"] == "h100" and gates_report["round_failed"] is False
    assert {c["id"] for c in report["checks"] if c["id"].startswith("paper3:")} == {f"paper3:G{n}" for n in range(6, 13)}
    assert report["args"]["paper3_host"] == "h100"


def test_a_host_with_an_unset_value_is_a_usage_error_before_anything_is_judged(tmp_path, capsys):
    root = build_round(tmp_path / "r", 1)
    config = config_file(tmp_path, h100={"t_min_s": None})
    with pytest.raises(SystemExit) as stop:
        run_p3(root, tmp_path, "--paper3-host", "h100", "--paper3-host-config", str(config), name="unset")
    assert stop.value.code == 2 and "unset in the host config: t_min_s" in capsys.readouterr().err
    assert not (tmp_path / "unset.json").exists()


@pytest.mark.parametrize("extra", [["--paper3-host", "h100"], ["--paper3-host-config", "x.json"], ["--host-capture", "a.json"]])
def test_the_hook_flags_go_together(tmp_path, extra):
    root = build_round(tmp_path / "r", 1)
    with pytest.raises(SystemExit) as stop:
        run_p3(root, tmp_path, *extra)
    assert stop.value.code == 2
