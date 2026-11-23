"""run_campaign.py in Paper 3 mode: the resolved design, the commands, the launches, cool-downs, resume and the gate plumbing.

Like test_run_campaign.py these tests put tiny stand-ins in place of run_experiment.py and preflight.py. The gate logic itself
(paper3_gates.py) is tested on synthetic session directories in test_paper3_gates.py; here only its wiring is.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import paper3_campaign as pc  # noqa: E402
import paper3_gates as pg  # noqa: E402
import run_campaign as rc  # noqa: E402
from paper3_fakes import RunBuilder, edit_results  # noqa: E402
from test_run_campaign import FAKE_RUN_EXPERIMENT, Rig, valid_lease  # noqa: E402,F401

COMMON = ["--root", "/r", "--data-root", "/d", "--jar", "/j.jar"]
LOCK = ["--paper3-lock-date", "20261116"]
REAL_CONFIG = json.loads((REPO / "paper3_hosts.json").read_text())

FAKE_P3_RUN_EXPERIMENT = FAKE_RUN_EXPERIMENT.replace(
    '"start_utc": "x"', '"start_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()')


# A run_experiment.py stand-in that lays down a prebuilt synthetic run (tests/paper3_fakes.py) instead of its own sessions, so the real
# gates of paper3_gates.py judge what the campaign collected.
FAKE_TEMPLATE_RUN_EXPERIMENT = FAKE_P3_RUN_EXPERIMENT.split("args.output.mkdir")[0] + '''
import shutil
from datetime import datetime, timezone
shutil.copytree(Path(os.environ["FAKE_TEMPLATES"]) / args.suite, args.output)
schedule = json.loads((args.output / "schedule.json").read_text())
for item in schedule:
    item["start_utc"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps({"event": "engine_started", **item}), flush=True)
    print(json.dumps({"event": "engine_complete", **item}), flush=True)
(args.output / "schedule.json").write_text(json.dumps(schedule))
'''


def plan_of(*args):
    cfg = rc.resolve_config(rc.parse_args([*COMMON, *args]))
    return cfg, rc.build_units(cfg)


def pending_allowlist_config(tmp_path):
    """The shipped config with the C-GCP allow-list set back to null: the state before amendment A6 filled it (2026-11-23). Keeps the refusal path covered."""
    document = json.loads(json.dumps(REAL_CONFIG))
    document["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"] = None
    path = tmp_path / "hosts-pending.json"
    path.write_text(json.dumps(document))
    return path


def test_the_shipped_config_carries_the_reviewed_c_gcp_allow_list_and_no_host_has_a_pending_value(capsys):
    allowed = REAL_CONFIG["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"]
    assert isinstance(allowed, list) and allowed and all(isinstance(item, str) for item in allowed) and allowed == sorted(allowed)       # filled by amendment A6
    for host, launch in (("a100", "1"), ("h100", "1"), ("c-gcp", "1"), ("c-vm", "1")):
        assert rc.main([*argv_for(host, launch), "--dry-run"]) == 0
        assert "host config unset: nothing" in capsys.readouterr().out, host


def argv_for(host, launch="1", *more):
    pin = ["--cpu-list", "0-12,96-108"] if host == "c-gcp" else []
    return ["--paper3-host", host, "--paper3-launch", launch, *LOCK, *pin, *more]


# ---- the resolved design --------------------------------------------------------------------------------------------

def test_a_gpu_host_launch_runs_its_square_with_the_lock_date_seed():
    cfg, units = plan_of(*argv_for("h100", "1"))
    assert cfg.formal and cfg.test_flags == [] and cfg.paper3.launch == "1"
    assert cfg.telemetry == {"gpu_energy_scope": "board", "energy_window_s": 10.0, "idle_baseline_seconds": 0, "g8_pulse_seconds": 3.0,
                             "g8_pulse_engines": "all", "session_cooldown_seconds": 0.0}
    assert [u.round for u in units] == [1, 2, 3, 4] and [u.sessions for u in units] == [32] * 4 and all(u.kind == "main" for u in units)
    assert [u.label for u in units] == ["ABDC", "BCAD", "CDBA", "DACB"]
    assert [[j.suite for j in u.jobs] for u in units] == [["baseline", "complex"], ["complex", "baseline"]] * 2
    assert [u.jobs[0].order for u in units][0] == ["spark_cpu", "spark_gpu", "polars_gpu", "polars"]       # seed 20261116
    design = rc.design_dict(cfg, units)
    assert design["seed"] == 20261116 and design["letters"] == {"A": "spark_cpu", "B": "spark_gpu", "C": "polars", "D": "polars_gpu"}
    assert design["design_doc"] == "docs/PAPER3_ENERGY_DESIGN.md" and "side_study" not in design
    assert design["paper3"]["launch_rounds"] == [1, 2, 3, 4] and design["totals"]["sessions"] == 128
    assert design["energy"] == {"gpu_energy_scope": "board", "energy_window_s": 10.0}


def test_the_second_launch_runs_rounds_5_to_8_in_reverse_row_order():
    cfg, units = plan_of(*argv_for("h100", "2"))
    assert [u.round for u in units] == [5, 6, 7, 8] and [u.label for u in units] == ["DACB", "CDBA", "BCAD", "ABDC"]
    assert [[j.suite for j in u.jobs][0] for u in units] == ["baseline", "complex", "baseline", "complex"]       # odd rounds baseline first
    assert units[0].attempt_path(0) == Path("/r/round-5") and units[0].attempt_path(1) == Path("/r/round-5-repeat-1")
    first, second = rc.design_dict(*plan_of(*argv_for("h100", "1"))), rc.design_dict(cfg, units)
    assert first["paper3"]["common_sha256"] == second["paper3"]["common_sha256"]
    assert first["paper3"]["schedule"] == second["paper3"]["schedule"] and first["main_rounds"] != second["main_rounds"]


def test_both_launches_on_one_instance_is_recorded_as_such():
    cfg, units = plan_of(*argv_for("a100", "both"))
    assert [u.round for u in units] == list(range(1, 9)) and rc.design_dict(cfg, units)["paper3"]["separate_launches"] is False
    assert cfg.allow_any_gpu, "A2: a non-H100 host passes --allow-any-gpu to the gate"


def test_rounds_is_a_test_flag_taking_a_prefix_of_the_launch():
    cfg, units = plan_of(*argv_for("h100", "2", "--rounds", "2"))
    assert [u.round for u in units] == [5, 6] and cfg.formal is False and cfg.test_flags == ["--rounds"]


def test_the_cpu_only_host_runs_two_engines_pinned_with_rapl_required():
    cfg, units = plan_of(*argv_for("c-gcp", "2"))
    assert [u.label for u in units] == ["BA", "AB", "AB", "BA"] and [u.sessions for u in units] == [16] * 4
    # lock 20261116: spark_cpu is A (it comes before polars in the four-engine assignment), polars is B
    assert [u.jobs[0].order for u in units] == [["polars", "spark_cpu"], ["spark_cpu", "polars"], ["spark_cpu", "polars"], ["polars", "spark_cpu"]]
    command = rc.job_command(units[0].jobs[0], Path("/r/round-5/baseline"), cfg)
    assert command[command.index("--cpu-energy") + 1] == "require" and command[command.index("--gpu-energy-scope") + 1] == "none"
    assert command[command.index("--idle-baseline-seconds") + 1] == "0"
    assert command[command.index("--cpu-list") + 1] == "0-12,96-108" and command[command.index("--energy-window-s") + 1] == "10.0"
    assert "--allow-any-gpu" not in rc.gate_command(units[0], Path("/r/round-5"), cfg, Path("/r/p"))


def test_the_vm_time_check_is_two_baseline_rounds_with_no_power_telemetry():
    cfg, units = plan_of(*argv_for("c-vm", "1"))
    assert [u.round for u in units] == [1, 2] and [u.sessions for u in units] == [6, 6] and [[j.suite for j in u.jobs] for u in units] == [["baseline"]] * 2
    assert cfg.scales == {"baseline": ["10", "30", "300"]}
    command = rc.job_command(units[0].jobs[0], Path("/r/round-1/baseline"), cfg)
    assert "--g8-pulse-seconds" not in command and "--g8-pulse-engines" not in command, "scope none runs no pulse"
    assert command[command.index("--scales") + 1:command.index("--scales") + 4] == ["10", "30", "300"]
    for flag, value in (("--power-telemetry", "off"), ("--cpu-energy", "off"), ("--idle-baseline-seconds", "0"),
                        ("--session-idle-seconds", "0"), ("--gpu-energy-scope", "none"), ("--energy-window-s", "10.0"),
                        ("--session-cooldown-seconds", "60.0")):
        assert command[command.index(flag) + 1] == value
    gate = rc.gate_command(units[0], Path("/r/round-1"), cfg, Path("/r/p"))
    assert gate[gate.index("--suites") + 1:gate.index("--suites") + 2] == ["baseline"] and "--complex-scales" not in gate
    with pytest.raises(rc.CampaignError, match="one launch"):
        rc.resolve_config(rc.parse_args([*COMMON, *argv_for("c-vm", "2")]))


def test_gpu_job_commands_carry_the_declared_scope_and_the_window():
    for host, scope in (("a100", "board"), ("h100", "board"), ("gh200", "module"), ("b200", "board")):
        cfg, units = plan_of(*argv_for(host))
        command = rc.job_command(units[0].jobs[0], Path("/o"), cfg)
        assert command[command.index("--gpu-energy-scope") + 1] == scope and command[command.index("--energy-window-s") + 1] == "10.0"
        assert "--cpu-energy" not in command and "--session-idle-seconds" not in command
        assert command[command.index("--idle-baseline-seconds") + 1] == "0", "P_idle is the two in-session brackets, not Paper 1's pre-session window"
        assert command[command.index("--g8-pulse-seconds") + 1] == "3.0" and command[command.index("--g8-pulse-engines") + 1] == "all", \
            "every session on a GPU host gets the G8 pulse, CPU and GPU engines"
        assert command[command.index("--session-cooldown-seconds") + 1] == "0.0", "the idle brackets of section 2.2 are the cool-down (design 3.2)"


def test_the_gate_command_of_a_paper3_round_is_paper3_strict_with_its_own_order():
    cfg, units = plan_of(*argv_for("h100", "2"))
    command = rc.gate_command(units[1], Path("/r/round-6"), cfg, Path("/r/round-6/preflight"), prior_reports=[Path("/r/p5.json")])
    assert "--formal" not in command and "--paper3" in command and command[command.index("--round") + 1] == "6"
    assert command[command.index("--expect-engine-order") + 1:command.index("--expect-engine-order") + 5] == ["polars", "polars_gpu", "spark_gpu", "spark_cpu"]
    assert command[command.index("--expect-suite-first") + 1] == "complex"
    assert command[command.index("--suites") + 1:command.index("--suites") + 3] == ["complex", "baseline"]
    assert command[command.index("--engines") + 1:command.index("--engines") + 5] == list(rc.ENGINES)
    assert command[command.index("--error-policy") + 1] == "classified" and command[command.index("--prior-reports") + 1] == "/r/p5.json"


def test_dry_run_prints_the_paper3_schedule_and_writes_nothing(capsys, tmp_path):
    assert rc.main([*argv_for("h100", "2"), "--dry-run", "--verbose"]) == 0
    out = capsys.readouterr().out
    assert "host h100 (gpu), launch 2, lock date 20261116 -> seed 20261116" in out
    assert "round 5 (square 2, row 4)  DACB  polars_gpu -> spark_cpu -> polars -> spark_gpu" in out and "THIS LAUNCH: rounds [5, 6, 7, 8], 128 sessions" in out
    assert "t_min: 1.0" in out and "cool-down: 0 s" in out and "--gpu-energy-scope board --energy-window-s 10.0" in out
    assert rc.main([*argv_for("gh200", "1"), "--dry-run"]) == 0
    assert "t_min: 1.0   host config unset: nothing" in capsys.readouterr().out, "GH200: P6 rerun and source check are in"
    assert rc.main([*argv_for("b200", "1"), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "t_min: 1.0   host config unset: nothing" in out and "--gpu-energy-scope board" in out, "B200: admitted by amendment A2, board scope fixed by the host class"
    assert rc.main([*argv_for("c-gcp", "1"), "--dry-run"]) == 0
    assert "host config unset: nothing" in capsys.readouterr().out, "the C-GCP allow-list is filled (amendment A6): nothing is pending"
    assert rc.main([*argv_for("c-gcp", "1"), "--dry-run", "--paper3-host-config", str(pending_allowlist_config(tmp_path))]) == 0
    assert "host config unset: ['allowed_scheduled']" in capsys.readouterr().out, "with the allow-list null it is the one pending value"


# ---- usage errors ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("extra,message", [
    (["--paper3-host", "h100", *LOCK], "--paper3-launch"),
    (["--paper3-host", "h100", "--paper3-launch", "1"], "--paper3-lock-date"),
    (["--paper3-host", "h100", "--paper3-launch", "1", *LOCK, "--only-side-study"], "side study"),
    (["--paper3-host", "h100", "--paper3-launch", "1", *LOCK, "--paper3-cpu-first", "polars"], "unrecognized arguments"),
    (["--paper3-host", "h100", "--paper3-launch", "1", *LOCK, "--socket", "0"], "takes no pin from the operator"),
    (["--paper3-host", "c-vm", "--paper3-launch", "1", *LOCK, "--cpu-list", "0-25"], "reads its own from the VM's topology"),
    (["--paper3-host", "c-gcp", "--paper3-launch", "1", *LOCK], "pins its engines"),
    (["--paper3-host", "h100", "--paper3-launch", "1", *LOCK, "--gpu-energy-scope", "module"], "conflicts"),
    (["--paper3-host", "h100", "--paper3-launch", "1", *LOCK, "--energy-window-s", "5"], "conflicts"),
    (["--paper3-host", "gh200", "--paper3-launch", "1", *LOCK, "--gpu-energy-scope", "board"], "conflicts"),
    (["--paper3-host", "b200", "--paper3-launch", "1", *LOCK, "--gpu-energy-scope", "module"], "conflicts"),
    (["--paper3-host", "b200", "--paper3-launch", "1", *LOCK, "--gpu-energy-scope", "none"], "conflicts"),
    (["--paper3-host", "c-vm", "--paper3-launch", "1", *LOCK, "--power-telemetry", "auto"], "conflicts"),
    (["--paper3-host", "h100", "--paper3-launch", "1", *LOCK, "--idle-baseline-seconds", "30"], "conflicts"),
    (["--paper3-host", "h100", "--paper3-launch", "1", *LOCK, "--engines-override", "polars", "nonsense"], "invalid choice"),
    (["--paper3-host", "c-gcp", "--paper3-launch", "1", *LOCK, "--cpu-list", "0-25", "--engines-override", "spark_gpu"], "runs"),
])
def test_usage_errors(extra, message, capsys):
    with pytest.raises(SystemExit) as raised:
        rc.parse_args([*COMMON, *extra])
    assert raised.value.code == 2 and message in capsys.readouterr().err


def test_the_cpu_only_dry_run_needs_no_letter_choice_and_a_bad_lock_date_is_a_design_error(capsys):
    assert rc.main([*COMMON, "--dry-run", "--paper3-host", "c-gcp", "--paper3-launch", "1", *LOCK, "--cpu-list", "0-12,96-108"]) == 0
    out = capsys.readouterr().out
    assert "letters {'A': 'spark_cpu', 'B': 'polars'} (from the four-engine assignment" in out
    assert rc.main([*COMMON, "--dry-run", "--paper3-host", "h100", "--paper3-launch", "1", "--paper3-lock-date", "2026-11-16"]) == rc.EXIT_DESIGN
    assert "YYYYMMDD" in capsys.readouterr().err


# ---- formal start: lock, host label, host config ---------------------------------------------------------------------------

def formal_cfg(host="h100", *more):
    cfg = rc.resolve_config(rc.parse_args([*COMMON, *argv_for(host, "1"), "--paper3-lock-commit", "c" * 40,
                                           "--paper3-lock-doi", "10.5281/zenodo.1", *more]))
    assert cfg.formal
    return cfg


def test_a_formal_campaign_needs_its_instance_type_and_the_hosts_config_values(monkeypatch, tmp_path):
    cfg = formal_cfg("h100")
    with pytest.raises(rc.CampaignError, match="records its instance type"):
        rc.paper3_startup_checks(cfg, resolved_pinning=False)
    cfg.host_label = "gpu_1x_a100_sxm4"
    with pytest.raises(rc.CampaignError, match="not an instance type of Paper 3 host h100"):
        rc.paper3_startup_checks(cfg, resolved_pinning=False)
    cfg.host_label = "gpu_1x_h100_sxm5"
    rc.paper3_startup_checks(cfg, resolved_pinning=False)
    rc.paper3_startup_checks(formal_cfg("gh200", "--host-label", "gpu_1x_gh200"), resolved_pinning=False)       # complete since the P6 rerun and the source check
    rc.paper3_startup_checks(formal_cfg("b200", "--host-label", "gpu_1x_b200_sxm6"), resolved_pinning=False)       # admitted (A2), board scope from the host class
    c_gcp_args = [*COMMON, *argv_for("c-gcp"), "--paper3-lock-commit", "c" * 40, "--paper3-lock-doi", "d", "--host-label", "c3-highmem-192-metal"]
    rc.paper3_startup_checks(rc.resolve_config(rc.parse_args(c_gcp_args)), resolved_pinning=False)                      # the reviewed allow-list is in the shipped config (A6)
    pending = rc.resolve_config(rc.parse_args([*c_gcp_args, "--paper3-host-config", str(pending_allowlist_config(tmp_path))]))
    with pytest.raises(rc.CampaignError, match="unset in .*: allowed_scheduled"):
        rc.paper3_startup_checks(pending, resolved_pinning=False)                                                          # a null allow-list still refuses a formal C-GCP campaign


def test_a_test_campaign_only_warns_about_unset_values(capsys, tmp_path):
    cfg = rc.resolve_config(rc.parse_args([*COMMON, *argv_for("c-gcp", "1"), "--rounds", "1", "--paper3-host-config", str(pending_allowlist_config(tmp_path))]))      # a pending allow-list
    assert not cfg.formal
    rc.paper3_startup_checks(cfg, resolved_pinning=False)
    assert "gates will not be evaluated" in capsys.readouterr().out
    filled = rc.resolve_config(rc.parse_args([*COMMON, *argv_for("c-gcp", "1"), "--rounds", "1"]))                                    # the shipped config: nothing pending, nothing to warn about
    rc.paper3_startup_checks(filled, resolved_pinning=False)
    assert "gates will not be evaluated" not in capsys.readouterr().out


def test_the_cpu_only_host_must_pin_26_vcpus_on_socket_0(c3):
    for flags, ok in ((["--cpu-list", "0-12,96-108"], True), (["--cpu-list", "0-24"], False), (["--cpu-list", "48-60,144-156"], False)):
        cfg = rc.resolve_config(rc.parse_args([*COMMON, "--paper3-host", "c-gcp", "--paper3-launch", "1", *LOCK,
                                               *flags, "--paper3-lock-commit", "c" * 40, "--paper3-lock-doi", "d", "--host-label", "c3-highmem-192-metal"]))
        cfg.pinning = rc.resolve_pin(cfg)
        if ok:
            rc.paper3_startup_checks(cfg, resolved_pinning=True)
        else:
            with pytest.raises(rc.CampaignError, match="socket 0"):
                rc.paper3_startup_checks(cfg, resolved_pinning=True)


def test_the_lock_is_verified_for_a_formal_campaign_and_skipped_for_a_test_campaign(monkeypatch, tmp_path):
    from test_paper3_campaign import AMENDMENTS, LOCKED, FakeGit
    doc = tmp_path / "PAPER3.md"
    doc.write_text(LOCKED + AMENDMENTS)
    monkeypatch.setattr(rc, "PAPER3_DESIGN_DOC", doc)
    monkeypatch.setattr(rc, "git_run", FakeGit())
    monkeypatch.setattr(rc, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: datetime(2026, 11, 20, tzinfo=timezone.utc))}))
    cfg = formal_cfg("h100")
    rc.verify_paper3_lock(cfg)
    assert cfg.paper3.lock_evidence["commit"] == "c" * 40 and cfg.paper3.lock_evidence["sections_1_11_unchanged"]
    cfg.paper3.lock["zenodo_doi"] = None
    with pytest.raises(rc.CampaignError, match="--paper3-lock-doi"):
        rc.verify_paper3_lock(cfg)
    cfg = formal_cfg("h100")
    cfg.paper3.lock["date_utc"] = "20261117"
    with pytest.raises(rc.CampaignError, match="seed is the lock date"):
        rc.verify_paper3_lock(cfg)
    informal = rc.resolve_config(rc.parse_args([*COMMON, *argv_for("h100"), "--rounds", "1"]))
    monkeypatch.setattr(rc, "git_run", lambda *a: (_ for _ in ()).throw(AssertionError("git must not be called")))
    rc.verify_paper3_lock(informal)
    assert informal.paper3.lock_evidence is None


def test_a_lock_date_in_the_future_is_refused_for_a_formal_campaign(monkeypatch):
    monkeypatch.setattr(rc, "git_run", lambda *a: (0, ""))
    cfg = formal_cfg("h100")
    cfg.paper3.lock["date_utc"] = "20990101"
    with pytest.raises(rc.CampaignError, match="future"):
        rc.verify_paper3_lock(cfg)


# ---- orchestration ----------------------------------------------------------------------------------------------------------

class P3Rig(Rig):
    def __init__(self, tmp_path, monkeypatch, host="c-vm", launch="1", config=None):
        super().__init__(tmp_path, monkeypatch)
        (tmp_path / "run_experiment.py").write_text(FAKE_P3_RUN_EXPERIMENT)
        self.host, self.launch = host, launch
        self.sleeps = []
        monkeypatch.setattr(rc.time, "sleep", lambda seconds: self.sleeps.append(seconds))
        monkeypatch.setattr(pg, "capture_host_exclusivity", lambda label, gpu, **kw: {"format": 1, "label": label, "gpu": gpu})
        self.config = config or self.write_config()

    def write_config(self):
        """The real config, with the host's gates switched off (no energy): the fake runner writes no counters."""
        document = json.loads(json.dumps(REAL_CONFIG))
        for entry in document["hosts"].values():
            entry["energy_measured"], entry["t_min_s"] = False, "not_applicable"
        path = self.tmp / "hosts.json"
        path.write_text(json.dumps(document))
        return path

    def args(self, *extra):
        base = super().args()
        return [*base, "--paper3-host", self.host, "--paper3-launch", self.launch, *LOCK, "--paper3-host-config", str(self.config), *extra]

    def round_dirs(self):
        return sorted(p.name for p in self.root.glob("round-*"))


@pytest.fixture
def p3rig(tmp_path, monkeypatch):
    r = P3Rig(tmp_path, monkeypatch)
    yield r
    r.restore()


@pytest.fixture
def h100rig(tmp_path, monkeypatch):
    r = P3Rig(tmp_path, monkeypatch, host="h100", launch="2")
    yield r
    r.restore()


# ---- the synthetic readiness data never feeds a campaign ------------------------------------------------------------------------

SYNTHETIC_MANIFEST = json.dumps({"format_version": 1, "scale_factor": 1, "synthetic_smoke": True,
                                 "dataset": "synthetic readiness data for the Paper 3 smoke test; not TPC-H, not a study dataset"})


def test_a_paper_3_campaign_refuses_a_data_root_marked_synthetic_smoke_before_anything_runs(p3rig, capsys):
    (p3rig.tmp / "data" / "sf1" / "manifest.json").write_text(SYNTHETIC_MANIFEST)
    assert p3rig.run() == rc.EXIT_DESIGN
    err = capsys.readouterr().err
    assert "synthetic readiness data" in err and "synthetic_smoke" in err and "sf1" in err
    assert not p3rig.calls() and not (p3rig.root / "campaign.json").exists() and not p3rig.round_dirs()      # nothing ran, nothing was written


def test_the_marker_is_checked_on_every_invocation_not_only_the_first(p3rig, capsys):
    assert p3rig.run() == 0
    (p3rig.tmp / "data" / "sf1" / "manifest.json").write_text(SYNTHETIC_MANIFEST)      # the data root is swapped between two invocations
    capsys.readouterr()
    assert p3rig.run() == rc.EXIT_DESIGN
    assert "synthetic readiness data" in capsys.readouterr().err


def test_only_a_true_marker_refuses_and_paper_1_is_not_touched(p3rig, tmp_path, monkeypatch):
    for manifest in ('{"synthetic_smoke": false}', "{}", "not json", "[1, 2]", '{"synthetic_smoke": null}'):
        (p3rig.tmp / "data" / "sf1" / "manifest.json").write_text(manifest)
        assert rc.synthetic_smoke_manifests(p3rig.tmp / "data", ["sf1"]) == [], manifest
    (p3rig.tmp / "data" / "sf1" / "manifest.json").write_text(SYNTHETIC_MANIFEST)
    assert rc.synthetic_smoke_manifests(p3rig.tmp / "data", ["sf1", "sf10"]) == ["sf1"]          # a scale with no manifest is the missing-manifest check's business
    (tmp_path / "p1").mkdir()
    paper1 = Rig(tmp_path / "p1", monkeypatch)
    (paper1.tmp / "data" / "sf1" / "manifest.json").write_text(SYNTHETIC_MANIFEST)
    assert paper1.run() == 0                                                              # Paper 1 mode has no such check (its campaigns are byte for byte unchanged)
    paper1.restore()


def test_a_launch_runs_its_rounds_and_records_the_paper3_design(p3rig):
    assert p3rig.run() == 0
    assert p3rig.round_dirs() == ["round-1", "round-2"]
    campaign = json.loads((p3rig.root / "campaign.json").read_text())
    design = campaign["design"]
    assert design["paper3"]["host"] == "c-vm" and design["paper3"]["lock"]["date_utc"] == "20261116" and design["formal"] is False
    assert campaign["provenance"]["design_doc_basis"].startswith("sections 1-11") and len(campaign["provenance"]["design_doc_full_sha256"]) == 64
    assert "docs/PAPER3_ENERGY_DESIGN.md" in campaign["provenance"]["source_sha256"] and "paper3_hosts.json" in campaign["provenance"]["source_sha256"]
    assert campaign["paper3"]["lock_evidence"] is None and len(campaign["paper3"]["host_entry_sha256"]) == 64
    runs = [c["argv"] for c in p3rig.calls("run")]
    assert [r[r.index("--engine-order") + 1:r.index("--engine-order") + 3] for r in runs] == [["spark_cpu", "polars"], ["polars", "spark_cpu"]]
    gate = p3rig.calls("preflight")[0]["argv"]
    assert "--formal" not in gate and "--paper3" in gate and gate[gate.index("--round") + 1] == "1" and "--suites" in gate
    assert p3rig.gates("round-1")["verdict"] == "pass" and p3rig.gates("round-2")["verdict"] == "pass"
    paper3 = p3rig.gates("round-1")["paper3"]
    assert paper3["evaluated"] is True and paper3["round_failed"] is False
    assert (p3rig.root / "round-1" / "paper3-gates.json").is_file() and (p3rig.root / "round-1" / "host-exclusivity-start.json").is_file()
    assert json.loads((p3rig.root / "round-1" / "host-exclusivity-end.json").read_text())["label"] == "end"
    status = p3rig.status()
    assert status["state"] == "complete" and status["paper3"]["squares"] == {"1": {"rounds": [1, 2], "accepted": [1, 2]}}
    assert status["paper3"]["squares_complete"] == [1]


def test_the_idle_brackets_are_the_cool_down_on_the_energy_hosts(h100rig):
    """Design 3.2 (2026-11-15): >= 150 s without query load between sessions, so the campaign sleeps nothing and tells run_experiment.py 0."""
    assert h100rig.run("--rounds", "2") == 0
    assert [r["output"].split("/")[-2:] for r in h100rig.calls("run")] == [["round-5", "baseline"], ["round-5", "complex"], ["round-6", "complex"], ["round-6", "baseline"]]
    assert [s for s in h100rig.sleeps if s > 1] == []
    assert all(r["argv"][r["argv"].index("--session-cooldown-seconds") + 1] == "0.0" for r in h100rig.calls("run"))


def test_the_time_only_vm_has_no_brackets_so_the_60_second_cool_down_applies_literally(p3rig):
    assert p3rig.run() == 0
    waits = [s for s in p3rig.sleeps if s > 1]
    assert len(waits) == 1 and 55 < waits[0] <= 60, "none before the first suite run, 60 s before the second"
    assert all(r["argv"][r["argv"].index("--session-cooldown-seconds") + 1] == "60.0" for r in p3rig.calls("run"))
    assert json.loads((p3rig.root / "campaign.json").read_text())["design"]["paper3"]["cooldown_s"] == 60.0


def test_cool_down_counts_the_time_since_the_last_suite_run_ended(p3rig):
    cfg = rc.resolve_config(rc.parse_args(p3rig.args()))
    p3rig.root.mkdir()
    campaign = rc.Campaign(cfg, rc.build_units(cfg), rc.Status(p3rig.root / "s.json", cfg, rc.build_units(cfg)), "h", None)
    slept = []
    campaign.sleep = slept.append
    campaign.cool_down()
    assert slept == [], "nothing ran yet"
    campaign.last_finished = datetime.now(timezone.utc) - timedelta(seconds=10)
    campaign.cool_down()
    assert len(slept) == 1 and 49 < slept[0] <= 50
    campaign.last_finished = datetime.now(timezone.utc) - timedelta(hours=3)
    campaign.cool_down()
    assert len(slept) == 1, "a resume long after the last run waits for nothing"
    paper1 = rc.resolve_config(rc.parse_args(["--rounds", "1"]))
    other = rc.Campaign(paper1, rc.build_units(paper1), campaign.status, "h", None)
    other.sleep, other.last_finished = slept.append, datetime.now(timezone.utc)
    other.cool_down()
    assert len(slept) == 1, "Paper 1 campaigns never cool down"


def test_the_cool_down_is_part_of_the_host_class_not_a_flag():
    with pytest.raises(SystemExit):
        rc.parse_args([*COMMON, *argv_for("h100"), "--paper3-cooldown-s", "30"])
    assert [pc.HOSTS[h].cooldown_s for h in ("a100", "h100", "gh200", "b200", "c-gcp", "c-vm")] == [0.0] * 5 + [60.0]


def test_a_resume_after_the_last_run_found_on_disk_cools_down_from_that_time(tmp_path, monkeypatch):
    rig = P3Rig(tmp_path, monkeypatch, host="c-vm")
    try:
        assert rig.run() == 0
        cfg = rc.resolve_config(rc.parse_args(rig.args()))
        units = rc.build_units(cfg)
        campaign = rc.Campaign(cfg, units, rc.Status(rig.root / "s2.json", cfg, units), "h", None)
        campaign.scan()
        attempt = json.loads((rig.root / "round-2" / "attempt.json").read_text())
        assert campaign.last_finished == datetime.fromisoformat(max(j["finished_utc"] for j in attempt["jobs"]))
    finally:
        rig.restore()


def test_a_second_invocation_runs_nothing_and_a_changed_design_is_refused(p3rig, capsys):
    assert p3rig.run() == 0
    runs = len(p3rig.calls("run"))
    assert p3rig.run() == 0 and len(p3rig.calls("run")) == runs
    capsys.readouterr()
    for change, extra in (("launch", ["--paper3-launch", "both"]), ("lock date", ["--paper3-lock-date", "20261120"])):
        argv = p3rig.args()
        for flag, value in zip(extra[::2], extra[1::2]):
            if flag in argv:
                argv[argv.index(flag) + 1] = value
            else:
                argv += [flag, value]
        p3rig.monkeypatch.setattr(sys, "argv", ["run_campaign.py", *argv])
        assert rc.main(argv) == rc.EXIT_DESIGN, change
        assert "different resolved design" in capsys.readouterr().err
    config = json.loads(p3rig.config.read_text())
    config["hosts"]["c-vm"]["t_min_basis"] = "edited"
    p3rig.config.write_text(json.dumps(config))
    assert p3rig.run() == rc.EXIT_DESIGN, "the host's config entry is part of the design"
    assert "different resolved design" in capsys.readouterr().err


def test_another_hosts_pending_values_do_not_change_this_hosts_design(p3rig):
    """C-VM's design digest is its own entry's: whatever C-GCP's allow-list is (filled by amendment A6, or null as at the lock, or a different list), the C-VM campaign resumes."""
    assert p3rig.run() == 0
    config = json.loads(p3rig.config.read_text())
    assert isinstance(config["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"], list), "filled in the shipped config"
    for value in (None, [], ["timer:other.timer"]):
        config["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"] = value
        p3rig.config.write_text(json.dumps(config))
        assert p3rig.run() == 0, value


def test_amendments_to_section_12_do_not_stop_a_running_campaign_but_a_change_to_section_5_does(p3rig, tmp_path, capsys):
    doc = tmp_path / "PAPER3.md"
    doc.write_text("# x\n\n## 5. Plan\nfixed\n\n## 11. Decisions\nfixed\n\n## 12. Amendments\nNone.\n")
    p3rig.monkeypatch.setattr(rc, "PAPER3_DESIGN_DOC", doc)
    assert p3rig.run() == 0
    doc.write_text(doc.read_text() + "\n### A1 - 2026-12-01 B200 admitted\nOutcome data seen: none.\n")
    assert p3rig.run() == 0
    drift = [json.loads(l) for l in (p3rig.root / "invocations.jsonl").read_text().splitlines()][-1]["drift"]
    assert any("design_doc_full_sha256_changed" in str(d) for d in drift)
    doc.write_text(doc.read_text().replace("## 5. Plan\nfixed", "## 5. Plan\nchanged"))
    capsys.readouterr()
    assert p3rig.run() == rc.EXIT_DESIGN
    assert "sections 1-11" in capsys.readouterr().err


def test_a_paper3_campaign_cannot_resume_as_a_paper1_campaign(p3rig, capsys):
    assert p3rig.run() == 0
    argv = Rig.args(p3rig)
    p3rig.monkeypatch.setattr(sys, "argv", ["run_campaign.py", *argv])
    assert rc.main(argv) == rc.EXIT_DESIGN
    assert "different resolved design" in capsys.readouterr().err


def passing_report(**fields):
    return {"round_failed": False, "round_failures": [], "ineligible": {}, "gates": {g: {"pass": 1, "fail": 0, "not_applicable": 0, "consequence": "c"} for g in pg.GATES},
            **fields}


def test_a_failing_round_gate_repeats_the_round_once_then_stops(h100rig, monkeypatch):
    seen = []

    def evaluate(runs, host, captures=()):
        seen.append((sorted(runs), len(captures)))
        return passing_report(round_failed=True, round_failures=["G6 baseline/sf1/polars: GPU counter source 'x'"])
    monkeypatch.setattr(pg, "evaluate_round", evaluate)
    assert h100rig.run("--rounds", "1") == rc.EXIT_STOPPED
    assert h100rig.round_dirs() == ["round-5", "round-5-repeat-1"]
    gates = h100rig.gates("round-5")
    assert gates["verdict"] == "fail" and gates["preflight"]["verdict"] == "pass" and "Paper 3 gate(s) failed the round" in gates["reasons"][0]
    assert gates["paper3"]["round_failures"] == ["G6 baseline/sf1/polars: GPU counter source 'x'"]
    assert seen[0] == (["baseline", "complex"], 2)
    assert h100rig.status()["state"] == "stopped"


def test_flag_gates_do_not_fail_a_round(h100rig, monkeypatch):
    monkeypatch.setattr(pg, "evaluate_round", lambda runs, host, captures=(): passing_report(ineligible={"baseline/sf1/polars": ["above_idle_reliable"]}))
    assert h100rig.run("--rounds", "1") == 0
    assert h100rig.gates("round-5")["verdict"] == "pass" and h100rig.gates("round-5")["paper3"]["ineligible_keys"] == 1


def test_a_gate_that_cannot_run_is_not_a_failed_round_in_a_formal_campaign(h100rig, monkeypatch):
    def refuse(runs, host, captures=()):
        raise pg.GateConfigError("host 'h100': unset in the host config: t_min_s")
    monkeypatch.setattr(pg, "evaluate_round", refuse)
    assert h100rig.run("--rounds", "1") == 0, "a test campaign records the refusal"
    assert h100rig.gates("round-5")["paper3"] == {"evaluated": False, "round_failed": False, "reason": "host 'h100': unset in the host config: t_min_s"}
    cfg = rc.resolve_config(rc.parse_args(h100rig.args()))
    campaign = rc.Campaign(cfg, rc.build_units(cfg), rc.Status(h100rig.root / "s3.json", cfg, []), "h", None)
    cfg.formal = True
    unit = rc.build_units(cfg)[0]
    path = h100rig.root / "round-5"
    with pytest.raises(rc.CampaignError, match="NOT counted as failed") as raised:
        campaign.run_paper3_gates(unit, path)
    assert raised.value.code == rc.EXIT_GATE_TOOL


def test_the_real_gates_run_on_the_rounds_files(h100rig):
    """With the unmodified gate code and a no-energy host the report is written and every gate is not applicable."""
    assert h100rig.run("--rounds", "1") == 0
    report = json.loads((h100rig.root / "round-5" / "paper3-gates.json").read_text())
    assert report["host"] == "h100" and report["round_failed"] is False
    assert {g: c["not_applicable"] for g, c in report["gates"].items() if g != "G9"} == {"G6": 4, "G7": 4, "G8": 4, "G10": 4, "G11": 4, "G12": 5}
    assert [s["key"] for s in report["runs"][0]["sessions"]][:2] == ["baseline/sf1/spark_cpu", "baseline/sf1/polars"]


def test_stop_after_round_uses_the_designs_round_numbers(h100rig):
    assert h100rig.run("--stop-after-round", "5", "--rounds", "3") == 0
    assert h100rig.round_dirs() == ["round-5"] and h100rig.status()["state"] == "paused"
    progress = h100rig.status()["paper3"]
    assert progress["squares"] == {"2": {"rounds": [5, 6, 7, 8], "accepted": [5]}}
    assert progress["squares_complete"] == [] and progress["confirmatory_rounds"] == [] and progress["sensitivity_only_rounds"] == [5]
    assert "complete squares only" in progress["rule"]


def test_the_status_says_which_square_is_complete_and_what_is_only_a_sensitivity(tmp_path, monkeypatch):
    """Lead's rule: confirmatory estimates use complete squares only; an incomplete square's accepted rounds are a sensitivity."""
    rig = P3Rig(tmp_path, monkeypatch, host="h100", launch="both")
    try:
        assert rig.run("--rounds", "3") == 0
        progress = rig.status()["paper3"]
        assert progress["squares"] == {"1": {"rounds": [1, 2, 3, 4], "accepted": [1, 2, 3]}}
        assert progress["squares_complete"] == [] and progress["confirmatory_rounds"] == [] and progress["sensitivity_only_rounds"] == [1, 2, 3]
        assert rig.run("--rounds", "4") == rc.EXIT_DESIGN, "a different --rounds is a different design"
    finally:
        rig.restore()
    (tmp_path / "second").mkdir()
    rig = P3Rig(tmp_path / "second", monkeypatch, host="h100", launch="both")
    try:
        assert rig.run("--rounds", "4") == 0
        progress = rig.status()["paper3"]
        assert progress["squares"] == {"1": {"rounds": [1, 2, 3, 4], "accepted": [1, 2, 3, 4]}}
        assert progress["squares_complete"] == [1] and progress["confirmatory_rounds"] == [1, 2, 3, 4] and progress["sensitivity_only_rounds"] == []
    finally:
        rig.restore()


def test_a_wrong_suite_order_is_a_conformance_failure(h100rig):
    script = (h100rig.tmp / "run_experiment.py").read_text()
    stamp = '__import__("datetime").datetime.now(__import__("datetime").timezone.utc)'
    (h100rig.tmp / "run_experiment.py").write_text(script.replace(stamp + ".isoformat()", "(" + stamp + " - __import__('datetime').timedelta(seconds=int(args.suite == 'complex') * 1000)).isoformat()"))
    assert h100rig.run("--rounds", "1") == rc.EXIT_STOPPED
    assert "suites did not start in the planned order" in json.dumps(h100rig.gates("round-5")["design_conformance"])


def test_the_launch_record_fields_reach_the_cost_block(p3rig):
    assert p3rig.run("--cost-provider", "lambda-labs", "--cost-hourly-price-usd", "4.89", "--cost-instance-id", "i-0abc",
                     "--cost-launch-utc", "2026-11-17T01:02:03Z", "--cost-credit-funded", "yes") == 0
    cost = json.loads((p3rig.root / "campaign.json").read_text())["cost"]
    assert cost["instance_id"] == "i-0abc" and cost["launch_utc"] == "2026-11-17T01:02:03+00:00" and cost["credit_funded"] == "yes"
    assert cost["hourly_price_usd"] == 4.89 and set(rc.COST_FIELDS) <= set(cost)


def test_the_lease_rules_are_the_existing_ones(p3rig, capsys):
    p3rig.lease_flags = []
    assert p3rig.run() == rc.EXIT_LEASE
    p3rig.lease_flags = ["--lease-name", valid_lease()]
    assert p3rig.run() == 0
    campaign = json.loads((p3rig.root / "campaign.json").read_text())
    assert campaign["lease"]["checked"] is True and campaign["lease"]["purpose"] == "test" and "ab12" not in json.dumps(campaign["lease"])


# ---- the real gates behind the campaign ---------------------------------------------------------------------------------------------

def clean_capture(label, gpu, **fields):
    capture = {"format": 1, "label": label, "gpu_compute_apps": {"returncode": 0, "apps": []} if gpu else None,
               "scheduled": {"systemd_timers": [], "cron_files": [], "crontab_entries": [], "at_jobs": []}}
    capture.update(fields)
    return capture


class GateRig(P3Rig):
    """An H100 launch whose suites are prebuilt synthetic runs: the campaign's gate runs the real paper3_gates.py on them."""

    def __init__(self, tmp_path, monkeypatch, **builder):
        super().__init__(tmp_path, monkeypatch, host="h100", launch="2", config=REPO / "paper3_hosts.json")
        (tmp_path / "run_experiment.py").write_text(FAKE_TEMPLATE_RUN_EXPERIMENT)
        self.templates = tmp_path / "templates"
        for suite in ("baseline", "complex"):
            RunBuilder(self.templates / suite, host="h100", suite=suite, engines=("spark_cpu", "polars"), scales=("1",), **builder).build()
        monkeypatch.setenv("FAKE_TEMPLATES", str(self.templates))
        self.capture = lambda label, gpu, **kw: clean_capture(label, gpu)
        monkeypatch.setattr(pg, "capture_host_exclusivity", lambda label, gpu, **kw: self.capture(label, gpu))


@pytest.fixture
def gaterig(tmp_path, monkeypatch):
    r = GateRig(tmp_path, monkeypatch)
    yield r
    r.restore()


def test_a_clean_synthetic_round_is_accepted_with_the_real_gates(gaterig):
    assert gaterig.run("--rounds", "1") == 0
    gates = gaterig.gates("round-5")
    assert gates["verdict"] == "pass" and gates["design_conformance"]["passed"] is True
    summary = gates["paper3"]
    assert summary["evaluated"] is True and summary["round_failed"] is False and summary["ineligible_keys"] == 0
    assert {g: c["pass"] for g, c in summary["gates"].items()} == {"G6": 4, "G7": 4, "G8": 4, "G9": 8, "G10": 4, "G11": 4, "G12": 5}
    report = json.loads((gaterig.root / "round-5" / "paper3-gates.json").read_text())
    assert report["host"] == "h100" and report["t_min_s"] == 1.0 and [r["suite"] for r in report["runs"]] == ["baseline", "complex"]


def test_a_g6_failure_in_the_collected_data_fails_the_round_repeats_it_once_and_stops(gaterig):
    edit_results(gaterig.templates / "baseline", "1", "polars", lambda r: r["gpu_energy_counter"].update(source="nvidia-smi:total_energy_consumption"))
    assert gaterig.run("--rounds", "1") == rc.EXIT_STOPPED
    assert gaterig.round_dirs() == ["round-5", "round-5-repeat-1"]
    gates = gaterig.gates("round-5")
    assert gates["verdict"] == "fail" and gates["preflight"]["verdict"] == "pass"
    assert gates["paper3"]["round_failures"][0].startswith("G6 baseline/sf1/polars: GPU counter source 'nvidia-smi:total_energy_consumption'")
    assert (gaterig.root / "round-5-repeat-1" / "paper3-gates.json").is_file()
    assert gaterig.status()["state"] == "stopped"


def test_eligibility_failures_in_the_collected_data_do_not_fail_the_round(tmp_path, monkeypatch):
    rig = GateRig(tmp_path, monkeypatch, post_idle_scale=1.5, skew=0.8)           # unstable idle (G10) and a clock 0.8 s off (G8)
    try:
        assert rig.run("--rounds", "1") == 0
        gates = rig.gates("round-5")
        assert gates["verdict"] == "pass" and gates["paper3"]["round_failed"] is False
        assert gates["paper3"]["gates"]["G10"]["fail"] == 4 and gates["paper3"]["gates"]["G8"]["fail"] == 4
        assert gates["paper3"]["gates"]["G6"]["fail"] == 0 and gates["paper3"]["ineligible_keys"] == 4
    finally:
        rig.restore()


def test_a_foreign_gpu_process_found_at_the_end_of_the_round_fails_it(gaterig):
    gaterig.capture = lambda label, gpu: clean_capture(label, gpu, **({"gpu_compute_apps": {"returncode": 0, "apps": ["777, other, 100"]}} if label == "end" else {}))
    assert gaterig.run("--rounds", "1") == rc.EXIT_STOPPED
    assert "G12 round: end: foreign GPU compute process" in gaterig.gates("round-5")["paper3"]["round_failures"][0]


def test_the_gates_are_rerun_on_a_round_that_finished_without_a_gate(gaterig):
    assert gaterig.run("--rounds", "1") == 0
    before = (gaterig.root / "round-5" / "paper3-gates.json").read_text()
    (gaterig.root / "round-5" / "gates.json").unlink()
    (gaterig.root / "round-5" / "paper3-gates.json").unlink()
    assert gaterig.run("--rounds", "1") == 0
    assert (gaterig.root / "round-5" / "paper3-gates.json").read_text() == before, "the same files give the same report"
    assert len(gaterig.calls("run")) == 2, "nothing was re-run, only gated"


def test_a_paper3_launch_interrupted_by_sigterm_resumes_with_a_repeat_and_keeps_its_cool_down_state(h100rig):
    import threading
    import time
    import os
    import signal

    def terminate_once_a_suite_is_running():
        deadline = time.time() + 60
        while time.time() < deadline and not h100rig.calls("run"):
            time.sleep(0.05)
        time.sleep(0.3)
        os.kill(os.getpid(), signal.SIGTERM)

    h100rig.set(hang_suite="baseline")
    threading.Thread(target=terminate_once_a_suite_is_running, daemon=True).start()
    assert h100rig.run("--rounds", "1", "--stop-after-round", "5") == rc.EXIT_INTERRUPTED
    assert h100rig.status()["state"] == "interrupted" and not (h100rig.root / "round-5" / "gates.json").exists()
    assert (h100rig.root / "round-5" / "host-exclusivity-start.json").is_file(), "the start capture was taken before the first suite"
    h100rig.set()
    assert h100rig.run("--rounds", "1", "--stop-after-round", "5") == 0
    assert (h100rig.root / "round-5" / "abandoned.json").is_file()
    assert h100rig.gates("round-5-repeat-1")["verdict"] == "pass" and h100rig.gates("round-5-repeat-1")["paper3"]["evaluated"] is True
    assert (h100rig.root / "round-5-repeat-1" / "host-exclusivity-start.json").is_file()
    progress = h100rig.status()["paper3"]
    assert progress["squares"] == {"2": {"rounds": [5, 6, 7, 8], "accepted": [5]}}, "the square is the design's, not the --rounds prefix"
    assert progress["squares_complete"] == [] and progress["confirmatory_rounds"] == [] and progress["sensitivity_only_rounds"] == [5]



# ---- the C-VM pin (design 3.1 "C-VM protocol") ------------------------------------------------------------------------------------------------

@pytest.fixture
def vm_topology(tmp_path, monkeypatch):
    """The sysfs of a c3-highmem-44 VM (22 cores x 2 threads, one NUMA node) as this process's topology."""
    import os
    import power_telemetry as pt
    from sysfs_fakes import make_cpu_tree

    def install(**shape):
        cpu_root, node_root = make_cpu_tree(tmp_path / f"vm-{len(list(tmp_path.glob('vm-*')))}", **{"sockets": 1, "cores": 22, "threads": 2, "nodes_per_socket": 1, **shape})
        monkeypatch.setattr(pt, "SYS_CPU_ROOT", cpu_root)
        monkeypatch.setattr(pt, "SYS_NODE_ROOT", node_root)
        monkeypatch.setattr(os, "sched_getaffinity", lambda pid: set(range(shape.get("cores", 22) * shape.get("threads", 2) * shape.get("sockets", 1))), raising=False)
    install()
    return install


def vm_cfg(*more):
    return rc.resolve_config(rc.parse_args([*COMMON, *argv_for("c-vm"), *more]))


def test_the_vm_pin_is_read_from_the_topology_and_becomes_part_of_the_design(vm_topology):
    cfg = vm_cfg("--host-label", "c3-highmem-44")
    rc.resolve_vm_pin(cfg, dry_run=False)
    assert cfg.pin_cpu_list == "0-12,22-34" and cfg.no_membind is True, "one NUMA node: nothing to bind"
    assert cfg.paper3.vm_pin["resolved"] is True and cfg.paper3.vm_pin["cpu_count"] == 26 and cfg.paper3.vm_pin["smt_siblings_reported"] is True
    units = rc.build_units(cfg)
    design = rc.design_dict(cfg, units)
    assert design["pinning"] == {"socket": None, "cpu_list": "0-12,22-34", "membind": False} and design["paper3"]["vm_pin"]["cpu_list"] == "0-12,22-34"
    command = rc.job_command(units[0].jobs[0], Path("/o"), cfg)
    assert command[command.index("--cpu-list") + 1] == "0-12,22-34" and "--no-membind" in command


def test_a_vm_with_several_numa_nodes_binds_memory_and_one_without_siblings_records_the_caveat(vm_topology):
    vm_topology(sockets=2, cores=22, nodes_per_socket=2)
    cfg = vm_cfg()
    rc.resolve_vm_pin(cfg, dry_run=False)
    assert cfg.no_membind is False and cfg.paper3.vm_pin["numa_nodes_reported"] == 4 and "--no-membind" not in rc.job_command(rc.build_units(cfg)[0].jobs[0], Path("/o"), cfg)
    vm_topology(sockets=1, cores=44, threads=1)
    cfg = vm_cfg()
    rc.resolve_vm_pin(cfg, dry_run=False)
    assert cfg.pin_cpu_list == "0-25" and cfg.paper3.vm_pin["smt_siblings_reported"] is False and "no SMT siblings" in cfg.paper3.vm_pin["caveat"]


def test_a_formal_vm_campaign_refuses_to_start_when_the_pin_cannot_be_resolved(vm_topology, tmp_path, monkeypatch):
    import power_telemetry as pt
    monkeypatch.setattr(pt, "SYS_CPU_ROOT", tmp_path / "no-cpu")
    cfg = vm_cfg("--paper3-lock-commit", "c" * 40, "--paper3-lock-doi", "d", "--host-label", "c3-highmem-44")
    assert cfg.formal
    with pytest.raises(rc.CampaignError, match="C-VM pin cannot be resolved") as raised:
        rc.resolve_vm_pin(cfg, dry_run=False)
    assert raised.value.code == rc.EXIT_DESIGN
    informal = vm_cfg("--rounds", "1")
    rc.resolve_vm_pin(informal, dry_run=False)
    assert informal.pin_cpu_list is None and informal.paper3.vm_pin["resolved"] is False, "a test campaign runs unpinned and says so"


def test_the_dry_run_does_not_read_the_topology_of_the_machine_it_runs_on(capsys):
    assert rc.main([*COMMON, *argv_for("c-vm"), "--dry-run"]) == 0
    assert "read from the VM's topology when the campaign starts" in capsys.readouterr().out


def test_a_formal_vm_campaign_needs_26_pinned_vcpus(vm_topology):
    cfg = vm_cfg("--paper3-lock-commit", "c" * 40, "--paper3-lock-doi", "d", "--host-label", "c3-highmem-44")
    rc.resolve_vm_pin(cfg, dry_run=False)
    cfg.pinning = rc.resolve_pin(cfg)
    rc.paper3_startup_checks(cfg, resolved_pinning=True)
    cfg.pinning = None
    with pytest.raises(rc.CampaignError, match="read from the VM's topology"):
        rc.paper3_startup_checks(cfg, resolved_pinning=True)


def test_a_vm_campaign_runs_pinned_and_records_what_the_pin_rests_on(vm_topology, tmp_path, monkeypatch):
    (tmp_path / "rig").mkdir()
    rig = P3Rig(tmp_path / "rig", monkeypatch, host="c-vm")
    try:
        assert rig.run() == 0
        campaign = json.loads((rig.root / "campaign.json").read_text())
        assert campaign["pinning"]["cpus"] == "0-12,22-34" and campaign["pinning"]["cpu_count"] == 26
        assert campaign["paper3"]["vm_pin"]["resolved"] is True and campaign["paper3"]["vm_pin"]["numa_nodes_reported"] == 1
        assert campaign["design"]["paper3"]["vm_pin"]["cpu_list"] == "0-12,22-34"
        first = rig.calls("run")[0]["argv"]
        assert first[first.index("--cpu-list") + 1] == "0-12,22-34" and "--no-membind" in first
        gate = rig.calls("preflight")[0]["argv"]
        assert gate[gate.index("--expect-affinity") + 1] == "0-12,22-34"
        assert rig.run() == 0, "the same VM resolves the same pin: a second invocation is a no-op"
    finally:
        rig.restore()
