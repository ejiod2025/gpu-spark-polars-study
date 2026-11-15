"""Paper 3 energy mode in run_experiment.py, run_campaign.py and the readers of gpu.csv.

The nvidia-smi calls of the runner are fakes (no GPU needed). The Paper 1 side of every change (no flag, same command lines
and files) is pinned in test_paper1_invariance.py.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import analyze_campaign as AC  # noqa: E402
import power_telemetry as pt  # noqa: E402
import run_campaign as rc  # noqa: E402
import run_experiment as rx  # noqa: E402
from test_power_telemetry import (  # noqa: E402,F401  (fixtures are imported by name so pytest registers them here)
    FakePopen, campaign_config, clean_env, hermetic_gpu_energy, host, layout, run_experiment)

SPEC = importlib.util.spec_from_file_location("paper3_feasibility", REPO / "tools" / "paper3_feasibility.py")
P3 = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault("paper3_feasibility", P3)
SPEC.loader.exec_module(P3)

BASE_QUERY = rx.GPU_TELEMETRY_QUERY
HELP_TEMPLATE = """List of valid properties to query for the switch "--query-gpu":

"timestamp"
The timestamp of where the query was made in format "YYYY/MM/DD HH:MM:SS.msec".

"driver_version"
The version of the installed NVIDIA display driver. This is an alphanumeric string.

"power.draw"
The last measured power draw for the entire board, in watts.

{extra}"clocks.sm"
Current frequency of SM (Streaming Multiprocessor) clock.
"""
FIELD_HELP = '"{name}"\nDescription of {name}.\n\n'
POWER_TEXT = """==============NVSMI LOG==============

Timestamp                                 : Sun Nov 15 18:00:00 2026
Driver Version                            : 570.148.08
CUDA Version                              : 12.8

Attached GPUs                             : 1
GPU 00000000:CB:00.0
    GPU Power Readings
        Average Power Draw                : 71.10 W
        Current Power Limit               : 700.00 W
        Default Power Limit               : 700.00 W
"""


def help_text(*fields):
    return HELP_TEMPLATE.format(extra="".join(FIELD_HELP.format(name=f) for f in fields))


ALL_POWER_FIELDS = ("power.draw.average", "power.draw.instant", "module.power.draw.average", "module.power.draw.instant")


@pytest.fixture
def smi(host, monkeypatch):
    """The `host` fixture's nvidia-smi plus the Paper 3 queries: field list, driver version, `-q -d POWER` text."""
    state = types.SimpleNamespace(fields=ALL_POWER_FIELDS, driver="570.148.08\n", help_returncode=0, power_calls=[],
                                  popen=[])
    base = host.capture

    def capture(command, timeout=None):
        if command[:2] == ["nvidia-smi", "--help-query-gpu"]:
            return {"command": command, "returncode": state.help_returncode,
                    "output": help_text(*state.fields) if state.help_returncode == 0 else ""}
        if command[:2] == ["nvidia-smi", "--query-gpu=driver_version"]:
            return {"command": command, "returncode": 0, "output": state.driver}
        if command == ["nvidia-smi", "-q", "-d", "POWER"]:
            state.power_calls.append(len(FakePopen.launched))        # sessions launched so far: start 0, end 1
            return {"command": command, "returncode": 0, "output": POWER_TEXT}
        return base(command, timeout)
    monkeypatch.setattr(rx, "capture", capture)
    original = rx.subprocess.Popen

    def popen(command, **kwargs):
        state.popen.append((list(command), kwargs))
        return original(command, **kwargs)
    monkeypatch.setattr(rx.subprocess, "Popen", popen)
    return state


def monitor_call(state):
    return next((c, k) for c, k in state.popen if c[0] == "nvidia-smi")


QUIET = ("--idle-baseline-seconds", "0", "--power-telemetry", "off")


# ---- gpu.csv fields -----------------------------------------------------------------------------------------------

def test_the_help_text_of_nvidia_smi_gives_the_listed_fields():
    fields = rx.parse_query_gpu_help(help_text("power.draw.average", "module.power.draw.instant"))
    assert fields == ["timestamp", "driver_version", "power.draw", "power.draw.average", "module.power.draw.instant", "clocks.sm"]
    assert rx.parse_query_gpu_help("") == [] and rx.parse_query_gpu_help(None) == []
    assert rx.parse_query_gpu_help("a line that merely mentions \"power.draw.average\" in prose") == []


def test_field_selection_by_scope_with_and_without_the_module_fields():
    listed = ["timestamp", "power.draw", *ALL_POWER_FIELDS]
    assert rx.gpu_telemetry_plan(listed, None) == {"query": BASE_QUERY, "extra_fields": [], "missing_fields": []}
    assert rx.gpu_telemetry_plan(listed, "none")["query"] == BASE_QUERY
    board = rx.gpu_telemetry_plan(listed, "board")                           # the driver lists module.* everywhere; board never asks
    assert board["query"] == BASE_QUERY + ",power.draw.average,power.draw.instant" and board["missing_fields"] == []
    module = rx.gpu_telemetry_plan(listed, "module")
    assert module["extra_fields"] == list(ALL_POWER_FIELDS) and module["query"] == BASE_QUERY + "," + ",".join(ALL_POWER_FIELDS)
    old_driver = rx.gpu_telemetry_plan(["timestamp", "power.draw"], "module")
    assert old_driver["query"] == BASE_QUERY and old_driver["extra_fields"] == [] and old_driver["missing_fields"] == list(ALL_POWER_FIELDS)
    partial = rx.gpu_telemetry_plan(["power.draw", "power.draw.instant", "module.power.draw.instant"], "module")
    assert partial["extra_fields"] == ["power.draw.instant", "module.power.draw.instant"]
    assert partial["missing_fields"] == ["power.draw.average", "module.power.draw.average"]
    assert BASE_QUERY.split(",") == rx.gpu_telemetry_plan(listed, "module")["query"].split(",")[:9]    # existing columns never move


def test_board_scope_appends_the_listed_power_fields_to_the_one_poller(monkeypatch, layout, smi):
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "board")
    command, kwargs = monitor_call(smi)
    assert command[1] == f"--query-gpu={BASE_QUERY},power.draw.average,power.draw.instant"
    assert command[2:] == ["--format=csv", "-lms", "200"] and kwargs["env"]["TZ"] == "UTC"
    assert len([c for c, _ in smi.popen if c[0] == "nvidia-smi"]) == 1               # one always-on poller, not two
    telemetry = environment["gpu_telemetry"]
    assert telemetry["query"] == command[1].removeprefix("--query-gpu=")
    assert telemetry["extra_fields"] == ["power.draw.average", "power.draw.instant"] and telemetry["missing_fields"] == []
    assert telemetry["driver_versions"] == ["570.148.08"] and telemetry["monitor_tz_env"] == "UTC"
    assert "power.draw.instant" in telemetry["fields_listed_by_driver"] and "module.power.draw.instant" in telemetry["fields_listed_by_driver"]
    assert telemetry["field_detection_command"] == ["nvidia-smi", "--help-query-gpu"]


def test_module_scope_adds_the_module_fields(monkeypatch, layout, smi):
    _, environment = run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "module")
    command, _ = monitor_call(smi)
    assert command[1].removeprefix("--query-gpu=").split(",")[9:] == list(ALL_POWER_FIELDS)
    assert environment["gpu_telemetry"]["extra_fields"] == list(ALL_POWER_FIELDS)


def test_a_driver_that_lists_fewer_fields_records_what_is_missing(monkeypatch, layout, smi):
    smi.fields = ("power.draw.instant",)
    _, environment = run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "board")
    assert monitor_call(smi)[0][1] == f"--query-gpu={BASE_QUERY},power.draw.instant"
    assert environment["gpu_telemetry"]["extra_fields"] == ["power.draw.instant"]
    assert environment["gpu_telemetry"]["missing_fields"] == ["power.draw.average"]


def test_module_scope_on_a_driver_without_module_fields_stops_before_anything_exists(monkeypatch, layout, smi, capsys):
    smi.fields = ("power.draw.average", "power.draw.instant")
    with pytest.raises(SystemExit) as raised:
        run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "module")
    assert "module.power.draw.average" in str(raised.value) and not layout.out.exists()


def test_a_scope_cannot_be_declared_when_nvidia_smi_lists_nothing(monkeypatch, layout, smi):
    smi.help_returncode = 1
    with pytest.raises(SystemExit) as raised:
        run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "board")
    assert "declared but" in str(raised.value) and not layout.out.exists()


def test_scope_none_leaves_the_poller_and_the_fields_alone(monkeypatch, layout, smi):
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "none")
    command, kwargs = monitor_call(smi)
    assert command[1] == f"--query-gpu={BASE_QUERY}" and "env" not in kwargs
    assert "query" not in environment["gpu_telemetry"] and not smi.power_calls
    assert environment["gpu_energy_counter"] == {"source": "disabled", "scope": "none", "scope_tag": "none"}
    assert "gpu_power_state" not in schedule[0]
    worker_flags = FakePopen.launched[0]
    assert worker_flags[worker_flags.index("--gpu-energy-scope") + 1] == "none"


# ---- what is recorded ----------------------------------------------------------------------------------------------

def test_the_declared_scope_and_window_are_recorded_and_reach_the_worker(monkeypatch, layout, smi):
    schedule, environment = run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "module",
                                           "--energy-window-s", "10")
    arguments = environment["arguments"]
    assert arguments["gpu_energy_scope"] == "module" and arguments["energy_window_s"] == 10.0
    block = environment["paper3_energy"]
    assert block["gpu_energy_scope"] == "module" and block["gpu_counter_scope_tag"] == "S1-module"
    assert block["energy_window_s"] == 10.0 and block["window_max_runs"] == 50 and set(block["scope_tags"]) == set(pt.SCOPE_TAGS)
    assert list(block["cpu_energy_scope_tags"]) == ["none"]                          # power telemetry is off here: nothing to tag
    command = FakePopen.launched[0]
    assert command[command.index("--gpu-energy-scope") + 1] == "module" and command[command.index("--energy-window-s") + 1] == "10.0"


def test_without_the_flags_the_paper3_arguments_are_not_recorded_or_passed(monkeypatch, layout, smi):
    _, environment = run_experiment(monkeypatch, layout, ["polars"], *QUIET)
    assert "gpu_energy_scope" not in environment["arguments"] and "energy_window_s" not in environment["arguments"]
    assert "paper3_energy" not in environment and "--gpu-energy-scope" not in FakePopen.launched[0]
    assert "--energy-window-s" not in FakePopen.launched[0] and not smi.power_calls
    assert monitor_call(smi)[1] == {"stdout": monitor_call(smi)[1]["stdout"], "stderr": rx.subprocess.STDOUT}


def test_the_probe_of_the_counter_is_told_the_scope(monkeypatch, layout, smi):
    calls = []
    original = rx.capture

    def capture(command, timeout=None):
        if "--probe-gpu-energy" in command:
            calls.append(command)
            return {"command": command, "returncode": 0, "output": json.dumps({"source": "x", "scope": command[-1]})}
        return original(command, timeout)
    monkeypatch.setattr(rx, "capture", capture)
    _, environment = run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "module")
    assert calls[0][-2:] == ["--gpu-energy-scope", "module"] and environment["gpu_energy_counter"]["scope"] == "module"


def test_nvidia_smi_power_text_is_kept_at_each_sessions_start_and_end(monkeypatch, layout, smi):
    schedule, _ = run_experiment(monkeypatch, layout, ["polars", "spark_cpu"], *QUIET, "--gpu-energy-scope", "board")
    assert smi.power_calls == [0, 1, 1, 2]                                           # start, end of session 1; start, end of session 2
    for item in schedule:
        state = item["gpu_power_state"]
        assert set(state) == {"start", "end"} and item["status"] == "complete"
        for label in ("start", "end"):
            entry = state[label]
            assert entry["file"] == f"{item['engine']}-gpu-power-{label}.txt" and entry["returncode"] == 0
            text = (layout.out / "sf1" / entry["file"]).read_text()
            assert "Current Power Limit               : 700.00 W" in text and "Power Readings" in text
            assert entry["sha256"] == hashlib.sha256(text.encode()).hexdigest() and entry["bytes"] == len(text.encode())
            assert entry["command"] == ["nvidia-smi", "-q", "-d", "POWER"]
        assert state["start"]["captured_utc"] <= state["end"]["captured_utc"]
    on_disk = json.loads((layout.out / "schedule.json").read_text())
    assert [i["gpu_power_state"]["start"]["file"] for i in on_disk] == [i["gpu_power_state"]["start"]["file"] for i in schedule]


def test_the_power_text_is_redacted_like_the_rest_of_the_inventory(monkeypatch, layout, smi):
    run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "board")
    text = (layout.out / "sf1" / "polars-gpu-power-start.txt").read_text()
    assert "00000000:CB:00.0" not in text and "GPU <redacted>" in text
    shutil_out = layout.out
    shutil_out.rename(layout.tmp / "first")
    run_experiment(monkeypatch, layout, ["polars"], *QUIET, "--gpu-energy-scope", "board", "--redact-gpu-identifiers", "off")
    assert "00000000:CB:00.0" in (layout.out / "sf1" / "polars-gpu-power-start.txt").read_text()


def test_cpu_energy_scope_tags_are_recorded_from_the_sampler(monkeypatch, layout, smi):
    def tree(root):
        for zone, name, energy in (("intel-rapl:0", "package-0", 10), ("intel-rapl:0:0", "dram", 5)):
            directory = root / zone
            directory.mkdir(parents=True)
            (directory / "name").write_text(name + "\n")
            (directory / "energy_uj").write_text(f"{energy}\n")
            (directory / "max_energy_range_uj").write_text("262143328850\n")
        return root
    from test_power_telemetry import Ticking, no_tools
    root = tree(layout.tmp / "powercap")
    monkeypatch.setattr(pt, "POWERCAP_ROOT", root)
    no_tools(monkeypatch)
    with Ticking(root):
        _, environment = run_experiment(monkeypatch, layout, ["polars"], "--idle-baseline-seconds", "0", "--power-interval-ms", "40",
                                        "--gpu-energy-scope", "board")
    assert "S2-all" in environment["paper3_energy"]["cpu_energy_scope_tags"]


# ---- invalid combinations -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("flags, engines, message", [
    (["--energy-window-s", "10"], ["polars"], "needs an explicit --gpu-energy-scope"),
    (["--gpu-energy-scope", "board", "--gpu-energy", "off"], ["polars"], "needs the counter"),
    (["--gpu-energy-scope", "board", "--energy-window-s", "0"], ["polars"], "positive"),
    (["--gpu-energy-scope", "board", "--energy-window-s", "-3"], ["polars"], "positive"),
    (["--gpu-energy-scope", "none"], ["polars", "spark_gpu"], "GPU engine"),
    (["--gpu-energy-scope", "auto"], ["polars"], "invalid choice"),
])
def test_invalid_energy_options_are_rejected_before_the_output_exists(monkeypatch, layout, smi, capsys, flags, engines, message):
    with pytest.raises(SystemExit) as raised:
        run_experiment(monkeypatch, layout, engines, *QUIET, *flags)
    assert raised.value.code == 2 and message in capsys.readouterr().err and not layout.out.exists()


# ---- run_campaign ---------------------------------------------------------------------------------------------------

def test_the_campaign_passes_the_declared_scope_and_window_and_records_them(clean_env, tmp_path):
    cfg = campaign_config("--root", str(tmp_path), "--gpu-energy-scope", "module", "--energy-window-s", "10")
    assert cfg.telemetry == {"gpu_energy_scope": "module", "energy_window_s": 10.0}
    command = rc.job_command(rc.build_units(cfg)[0].jobs[0], Path("/out"), cfg)
    assert command[command.index("--gpu-energy-scope") + 1] == "module" and command[command.index("--energy-window-s") + 1] == "10.0"
    design = rc.design_dict(cfg, rc.build_units(cfg))
    assert design["energy"] == {"gpu_energy_scope": "module", "energy_window_s": 10.0}
    cfg.root.mkdir(exist_ok=True)
    prov = {"design_doc_sha256": "a" * 64, "git": {"commit": None}, "source_sha256": {"run_campaign.py": "b" * 64},
            "inputs": {"manifest_sha256": {}, "inputs_reports_sha256": {}, "rapids_jar_sha256": None}}
    rc.start_campaign_file(cfg, design, prov, types.SimpleNamespace(accept_design_doc_change=False))
    stored = json.loads((tmp_path / "campaign.json").read_text())
    assert stored["telemetry"] == {"gpu_energy_scope": "module", "energy_window_s": 10.0} and stored["design"]["energy"]["gpu_energy_scope"] == "module"
    invocation = json.loads((tmp_path / "invocations.jsonl").read_text().splitlines()[0])
    assert invocation["telemetry"]["gpu_energy_scope"] == "module"


def test_a_campaign_cannot_resume_with_another_declared_scope(clean_env, tmp_path):
    prov = {"design_doc_sha256": "a" * 64, "git": {"commit": None}, "source_sha256": {"run_campaign.py": "b" * 64},
            "inputs": {"manifest_sha256": {}, "inputs_reports_sha256": {}, "rapids_jar_sha256": None}}

    def start(*flags):
        cfg = campaign_config("--root", str(tmp_path), *flags)
        cfg.root.mkdir(exist_ok=True)
        design = rc.design_dict(cfg, rc.build_units(cfg))
        return rc.start_campaign_file(cfg, design, prov, types.SimpleNamespace(accept_design_doc_change=False))
    first = start("--gpu-energy-scope", "board", "--energy-window-s", "10")
    assert start("--gpu-energy-scope", "board", "--energy-window-s", "10") == first
    for other in (("--gpu-energy-scope", "module", "--energy-window-s", "10"), ("--gpu-energy-scope", "board", "--energy-window-s", "20"),
                  ("--gpu-energy-scope", "board"), ()):
        with pytest.raises(rc.CampaignError) as raised:
            start(*other)
        assert raised.value.code == rc.EXIT_DESIGN and "different resolved design" in str(raised.value)


@pytest.mark.parametrize("flags", [["--energy-window-s", "10"], ["--gpu-energy-scope", "board", "--gpu-energy", "off"],
                                   ["--gpu-energy-scope", "board", "--energy-window-s", "0"],
                                   ["--gpu-energy-scope", "board", "--energy-window-s", "soon"], ["--gpu-energy-scope", "inferred"]])
def test_the_campaign_rejects_invalid_energy_options(clean_env, capsys, flags):
    with pytest.raises(SystemExit) as raised:
        rc.parse_args(flags)
    assert raised.value.code == 2


def test_the_campaign_refuses_gpu_engines_on_a_host_declared_to_have_no_gpu_counter(clean_env, tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "running_workloads", lambda: [])
    jar = tmp_path / "rapids.jar"
    jar.write_bytes(b"")
    cfg = campaign_config("--root", str(tmp_path), "--jar", str(jar), "--gpu-energy-scope", "none")
    prov = {"inputs": {"inputs_reports_sha256": {}, "manifest_sha256": {}}}
    with pytest.raises(rc.CampaignError) as raised:
        rc.startup_checks(cfg, rc.build_units(cfg), prov)
    assert raised.value.code == rc.EXIT_DESIGN and "polars_gpu" in str(raised.value) and "spark_gpu" in str(raised.value)


# ---- the readers of gpu.csv keep working when fields are appended -------------------------------------------------------

PAPER1_HEADER = ("timestamp, index, utilization.gpu [%], utilization.memory [%], memory.used [MiB], power.draw [W], temperature.gpu, "
                 "clocks.current.sm [MHz], clocks.current.memory [MHz]")
PAPER3_HEADER = PAPER1_HEADER + ", power.draw.average [W], power.draw.instant [W], module.power.draw.average [W], module.power.draw.instant [W]"


def write_gpu_csv(path, header, extra):
    rows = [header]
    for i in range(6):
        base = f"2026/10/30 03:57:0{i}.323, 0, 55 %, 3 %, 4096 MiB, {100 + i}.00 W, 41, 1410 MHz, 1593 MHz"
        rows.append(base + extra.format(i=i))
    path.write_text("\n".join(rows) + "\n")
    return path


def test_analysis_readers_take_power_draw_not_an_appended_power_field(tmp_path):
    old = write_gpu_csv(tmp_path / "old.csv", PAPER1_HEADER, "")
    new = write_gpu_csv(tmp_path / "new.csv", PAPER3_HEADER, ", 7{i}.00 W, 8{i}.00 W, 9{i}.00 W, 6{i}.00 W")
    assert AC.read_gpu_power(new) == AC.read_gpu_power(old)
    assert AC.read_gpu_power(new)[1] == [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    assert AC.read_gpu_samples(new) == AC.read_gpu_samples(old)
    assert AC.read_gpu_samples(new)["power"][0] == 100.0 and AC.read_gpu_samples(new)["sm_clock"][0] == 1410.0


def test_the_feasibility_reader_accepts_appended_columns_and_still_rejects_a_changed_header(tmp_path):
    old = write_gpu_csv(tmp_path / "old.csv", PAPER1_HEADER, "")
    new = write_gpu_csv(tmp_path / "new.csv", PAPER3_HEADER, ", 70.00 W, 80.00 W, [N/A], [N/A]")
    a, b = P3.read_gpu_csv(old), P3.read_gpu_csv(new)
    assert a.header_ok and b.header_ok and b.error is None
    for field in ("rows", "incomplete_rows", "draw_ok_rows", "draw_null_rows", "draw_na_rows", "draw_unparseable_rows",
                  "bad_timestamp_rows", "indices", "t_us"):
        assert getattr(a, field) == getattr(b, field), field
    assert a.rows == 6 and a.draw_ok_rows == 6
    renamed = write_gpu_csv(tmp_path / "renamed.csv", PAPER1_HEADER.replace("power.draw [W]", "power.draw.average [W]"), "")
    assert not P3.read_gpu_csv(renamed).header_ok
    shorter = tmp_path / "short.csv"
    shorter.write_text(PAPER1_HEADER.rsplit(",", 1)[0] + "\n" + "2026/10/30 03:57:00.323, 0, 55 %, 3 %, 4096 MiB, 100.00 W, 41, 1410 MHz\n")
    assert not P3.read_gpu_csv(shorter).header_ok
