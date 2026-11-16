"""The Paper 3 readiness (smoke) test of the harness: the synthetic workload and its explicit flag, the campaign-parity command,
the data generator, the checklist (tools/paper3_smoke/check_smoke.py) and the H100 driver (tools/paper3_smoke/run_h100_smoke.sh).

No GPU and no cloud. The checklist is run on synthetic run directories (tests/paper3_fakes.py) completed with the files the smoke
driver adds; the driver against the fake ssh/scp/lambda.sh of test_paper3_probe_drivers.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "tools" / "paper3_smoke"
for path in (REPO, REPO / "tests", SMOKE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import check_smoke as cs  # noqa: E402
import generate_smoke_data as gen  # noqa: E402
import paper3_gates as pg  # noqa: E402
import run_campaign as rc  # noqa: E402
import smoke_command  # noqa: E402
import smoke_workloads as sw  # noqa: E402
from paper3_fakes import RunBuilder, edit_json, edit_results, edit_schedule_item, host_gates  # noqa: E402

ENGINES = ("polars", "polars_gpu", "spark_cpu", "spark_gpu")
pl = pytest.importorskip("polars")
np = pytest.importorskip("numpy")


# ---- the synthetic workload -------------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tiny_data(tmp_path_factory):
    root = tmp_path_factory.mktemp("smoke-data")
    gen.generate(root, {"small": 3000, "facts": 60000, "dims": 500}, {"small": 1, "facts": 3, "dims": 1}, gen.SEED)
    return root


def test_the_suite_has_the_module_contract_of_the_other_suites():
    assert sw.QUERY_IDS == ["sm_light", "sm_join", "sm_scan"] and sw.SUITE_VERSION == "smoke-v1"
    assert set(sw.DESCRIPTIONS) == set(sw.REQUIRED_TABLES) == set(sw.QUERY_IDS)
    tables = {t for needed in sw.REQUIRED_TABLES.values() for t in needed}
    assert tables == {"small", "facts", "dims"} and set(sw.FLOAT_COLUMNS) == tables
    assert sw.spark_sql("sm_join").startswith("SELECT") and "FROM facts" in sw.spark_sql("sm_join")
    with pytest.raises(KeyError):
        sw.polars_query("q01", {})


def test_the_polars_and_sql_versions_of_every_query_agree(tiny_data):
    duckdb = pytest.importorskip("duckdb")
    folder = tiny_data / "sf1"
    tables = {t: pl.scan_parquet(str(folder / t / "*.parquet")) for t in ("small", "facts", "dims")}
    connection = duckdb.connect()
    for table in tables:
        connection.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{folder / table}/*.parquet')")
    for query in sw.QUERY_IDS:
        mine = sw.polars_query(query, tables).collect().to_dicts()
        reference = connection.execute(sw.spark_sql(query)).to_arrow_table().to_pylist()
        assert mine and cs.rows_match(reference, mine) is None, (query, cs.rows_match(reference, mine))
    assert len(sw.polars_query("sm_light", tables).collect()) == gen.SMALL_GROUPS
    assert len(sw.polars_query("sm_join", tables).collect()) == gen.DIM_GROUPS


def test_the_generator_is_deterministic_and_keeps_its_keys_in_range(tiny_data, tmp_path):
    again = tmp_path / "again"
    gen.generate(again, {"small": 3000, "facts": 60000, "dims": 500}, {"small": 1, "facts": 3, "dims": 1}, gen.SEED)
    one, two = json.loads((tiny_data / "sf1" / "manifest.json").read_text()), json.loads((again / "sf1" / "manifest.json").read_text())
    assert one["synthetic_smoke"] is True and one["scale_factor"] == 1 and one["format_version"] == 1
    assert {t: m["rows"] for t, m in one["tables"].items()} == {"small": 3000, "facts": 60000, "dims": 500}
    for table in ("small", "facts", "dims"):
        a = pl.read_parquet(str(tiny_data / "sf1" / table / "*.parquet"))
        b = pl.read_parquet(str(again / "sf1" / table / "*.parquet"))
        assert a.equals(b) and a.height == one["tables"][table]["rows"]
        assert [f["sha256"] for f in one["tables"][table]["files"]] == [f["sha256"] for f in two["tables"][table]["files"]]
    facts = pl.read_parquet(str(tiny_data / "sf1" / "facts" / "*.parquet"))
    assert facts["f_key"].min() >= 0 and facts["f_key"].max() < 500 and facts["f_grp"].max() < gen.FACT_GROUPS
    assert 0.0 <= facts["f_x"].min() and facts["f_x"].max() < 1.0 and facts["f_y"].dtype == pl.Float64
    assert pl.read_parquet(str(tiny_data / "sf1" / "dims" / "*.parquet"))["d_w"].min() >= 0.5
    with pytest.raises(SystemExit, match="already exists"):
        gen.generate(again, {"small": 10, "facts": 10, "dims": 10}, {"small": 1, "facts": 1, "dims": 1}, 1)


def worker_command(data, output, *more):
    return [sys.executable, str(REPO / "worker.py"), "--engine", "polars", "--data", str(data / "sf1"), "--output", str(output), *more]


def test_the_worker_runs_the_smoke_suite_only_with_the_explicit_flag(tiny_data, tmp_path):
    done = subprocess.run(worker_command(tiny_data, tmp_path / "w", "--suite", "smoke"), capture_output=True, text=True)
    assert done.returncode == 2 and "--suite smoke and --synthetic-smoke go together" in done.stderr
    done = subprocess.run(worker_command(tiny_data, tmp_path / "w2", "--synthetic-smoke"), capture_output=True, text=True)
    assert done.returncode == 2 and "no other suite accepts the flag" in done.stderr
    done = subprocess.run(worker_command(tiny_data, tmp_path / "w3", "--suite", "smoke", "--synthetic-smoke", "--runs", "1", "--gpu-energy-scope", "none",
                                         "--energy-window-s", "0.2", "--session-idle-seconds", "0"), capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    results = json.loads((tmp_path / "w3" / "results.json").read_text())
    assert results["suite"] == "smoke" and results["suite_version"] == "smoke-v1" and results["synthetic_smoke"] is True
    assert sorted(results["queries"]) == sorted(sw.QUERY_IDS) and results["query_errors"] == []
    measured = [r for r in results["records"] if r["phase"] == "measured"]
    assert sorted(r["query"] for r in measured) == sorted(sw.QUERY_IDS)
    assert all(r["energy_window"]["n"] >= 1 and r["energy_window"]["stop_reason"] in ("target_reached", "max_runs") for r in measured)


def runner_command(*more):
    return [sys.executable, str(REPO / "run_experiment.py"), "--data-root", "/nonexistent", "--output", "/nonexistent/out", "--jar", "/nonexistent/x.jar",
            "--engines", "polars", *more]


def test_the_runner_demands_both_flags_together_before_it_does_anything():
    for more, message in ((("--suite", "smoke"), "go together"), (("--synthetic-smoke",), "go together"),
                          (("--suite", "complex", "--synthetic-smoke"), "go together")):
        done = subprocess.run(runner_command(*more), capture_output=True, text=True, timeout=60)
        assert done.returncode == 2 and message in done.stderr, (more, done.stderr[-500:])
    assert not Path("/nonexistent/out").exists()


# ---- run_campaign cannot express it ------------------------------------------------------------------------------------------

COMMON = ["--root", "/r", "--data-root", "/d", "--jar", "/j.jar"]
LOCK = ["--paper3-lock-date", "20261116"]


@pytest.mark.parametrize("extra", [["--synthetic-smoke"], ["--suite", "smoke"], ["--suite", "smoke", "--synthetic-smoke"]])
def test_run_campaign_refuses_the_smoke_flags(extra, capsys):
    for base in ([], ["--paper3-host", "h100", "--paper3-launch", "1", *LOCK]):
        with pytest.raises(SystemExit) as exit_:
            rc.parse_args([*COMMON, *base, *extra])
        assert exit_.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err


def test_no_job_run_campaign_builds_can_run_the_smoke_suite():
    assert "smoke" not in rc.SUITES
    for host in ("h100", "a100", "gh200", "b200", "c-gcp"):
        pin = ["--cpu-list", "0-12,96-108"] if host == "c-gcp" else ["--gpu-energy-scope", "board"] if host == "b200" else []
        cfg = rc.resolve_config(rc.parse_args([*COMMON, "--paper3-host", host, "--paper3-launch", "1", *LOCK, *pin]))
        for unit in rc.build_units(cfg):
            for job in unit.jobs:
                command = rc.job_command(job, Path("/out") / job.out_rel, cfg)
                assert "smoke" not in command and "--synthetic-smoke" not in command and command[command.index("--suite") + 1] in rc.SUITES
    cfg = rc.resolve_config(rc.parse_args([*COMMON]))                      # and a Paper 1 campaign
    for unit in rc.build_units(cfg):
        for job in unit.jobs:
            assert "--synthetic-smoke" not in rc.job_command(job, Path("/out") / job.out_rel, cfg)


# ---- the campaign-parity command ----------------------------------------------------------------------------------------------

def test_the_smoke_command_is_the_campaigns_round_1_command_with_only_the_workload_and_paths_swapped():
    command = smoke_command.build("/h/py", "/h/data", "/h/run", "/h/rapids.jar")
    cfg = rc.resolve_config(rc.parse_args([*COMMON, "--paper3-host", "h100", "--paper3-launch", "1", *LOCK]))
    job = rc.build_units(cfg)[0].jobs[0]
    campaign = rc.job_command(job, Path("/h/run"), cfg)
    assert command[0] == "/h/py" and command[1] == campaign[1] and command[-1] == "--synthetic-smoke"

    def flags(cmd):
        out, key = {}, None
        for part in cmd[2:]:
            if part.startswith("--"):
                key = part
                out[key] = []
            else:
                out[key].append(part)
        return out
    mine, theirs = flags(command), flags(campaign)
    changed = {k for k in set(mine) | set(theirs) if mine.get(k) != theirs.get(k)}
    assert changed == {"--suite", "--scales", "--data-root", "--jar", "--synthetic-smoke"}, changed
    assert mine["--suite"] == ["smoke"] and mine["--scales"] == ["1"] and mine["--data-root"] == ["/h/data"]
    for flag, value in (("--gpu-energy-scope", ["board"]), ("--energy-window-s", ["10.0"]), ("--g8-pulse-seconds", ["3.0"]),
                        ("--g8-pulse-engines", ["all"]), ("--runs", ["3"]), ("--cpus", ["26"]), ("--keep-going", []),
                        ("--idle-baseline-seconds", ["0"]), ("--session-cooldown-seconds", ["0.0"])):
        assert mine[flag] == value, flag
    assert sorted(mine["--engines"]) == sorted(ENGINES) and sorted(mine["--engine-order"]) == sorted(ENGINES)
    assert "--session-idle-seconds" not in mine, "the in-session idle windows keep the runner's 30 s / 60 s defaults, as in a campaign"
    assert smoke_command.build("/h/py", "/h/data", "/h/run", "/h/x.jar", runs=1)[command.index("--runs") + 1] == "1"
    done = subprocess.run([sys.executable, str(SMOKE / "smoke_command.py"), "--python", "/h/py", "--data-root", "/h/data", "--output", "/h/run",
                           "--jar", "/h/rapids.jar", "--json"], capture_output=True, text=True)
    assert done.returncode == 0 and json.loads(done.stdout)[0] == "/h/py"


# ---- the checklist -----------------------------------------------------------------------------------------------------------

QUERY = ("timestamp,index,utilization.gpu,utilization.memory,memory.used,power.draw,temperature.gpu,clocks.sm,clocks.mem,"
         "power.draw.average,power.draw.instant,clocks_event_reasons.active")
LIMIT_FIELDS = ["power.limit", "enforced.power.limit", "power.default_limit", "power.min_limit", "power.max_limit"]


def per_run_seconds(light=0.01, join_cpu=1.6, join_gpu=1.2, scan=1.1):
    table = {}
    for engine in ENGINES:
        table[(engine, "sm_light")] = light
        table[(engine, "sm_join")] = join_gpu if engine.endswith("gpu") else join_cpu
        table[(engine, "sm_scan")] = scan
    return table


def rewrite_memory(path, builder):
    """memory.used: a few MiB with no CUDA context, 611 MiB while a GPU engine's worker holds one (the builder knows when)."""
    lines = path.read_text().splitlines()
    column = next(i for i, h in enumerate(c.strip() for c in lines[0].split(",")) if h.startswith("memory.used"))
    out = [lines[0]]
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(",")]
        t = datetime.strptime(cells[0], "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()
        cells[column] = "611 MiB" if any(a <= t - 0.15 < b for a, b in builder.context_open_ranges) else "4 MiB"
        out.append(", ".join(cells))
    path.write_text("\n".join(out) + "\n")


def build_evidence(tmp_path, per_run=None, **builder_kwargs):
    """What run_h100_smoke.sh leaves in docs/paper3-evidence/smoke/h100-<stamp>/, for a clean run (a synthetic one)."""
    ev = tmp_path / "ev"
    builder = RunBuilder(ev / "run", host="h100", suite="smoke", engines=ENGINES, queries=sw.QUERY_IDS, per_run=per_run or per_run_seconds(),
                         **builder_kwargs).build()
    rewrite_memory(ev / "run" / "gpu.csv", builder)
    edit_json(ev / "run" / "environment.json", lambda env: env.update({
        "arguments": {"suite": "smoke", "synthetic_smoke": True, "engines": list(ENGINES), "engine_order": list(ENGINES), "runs": 3, "cpus": 26,
                      "gpu_energy_scope": "board", "energy_window_s": 10.0, "g8_pulse_seconds": 3.0, "g8_pulse_engines": "all"},
        "paper3_energy": {"gpu_energy_scope": "board", "gpu_counter_scope_tag": "S1-counter", "session_idle_seconds": 30.0,
                          "session_idle_settle_seconds": 0.5},       # the builder's idle windows settle 0.5 s
        "gpu_energy_counter": {"source": "nvml-ctypes:nvmlDeviceGetTotalEnergyConsumption", "scope": "board", "scope_tag": "S1-counter"},
        "gpu_telemetry": {**env["gpu_telemetry"], "query": QUERY, "extra_fields": ["power.draw.average", "power.draw.instant", "clocks_event_reasons.active"],
                          "missing_fields": [], "fields_listed_by_driver": ["timestamp", "power.draw.average", "clocks_event_reasons.active"],
                          "driver_versions": ["570.148.08"], "started": True},
        "inventory": [{"command": ["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"],
                       "output": "name, driver_version, memory.total [MiB]\nNVIDIA H100 80GB HBM3, 570.148.08, 81559 MiB\n"}],
        "power_telemetry": {"enabled": True, "started": True, "status": "unavailable", "cpu_energy_source": None, "node_power_source": None,
                            "interval_seconds": 0.2, "reasons": ["no RAPL zones in a VM"]}}))
    for engine in ENGINES:
        def complete(results, engine=engine):
            results.update(suite="smoke", synthetic_smoke=True, queries=list(sw.QUERY_IDS), query_execution_status="complete", query_errors=[])
            for record in results["records"]:
                if record["phase"] == "warmup":
                    record["rows"] = [{"k": 1, "total": 12.5 + (1e-12 if engine == "spark_gpu" else 0.0), "n": 7}]
        edit_results(ev / "run", "1", engine, complete)

        def limits(item):
            for label in ("start", "end"):
                record = item["gpu_power_state"][label]
                record["power_limits_w"] = {"gpu": {"current_w": 700.0, "requested_w": 700.0, "default_w": 700.0, "min_w": None, "max_w": None}}
                record["power_limits_query"] = {"command": ["nvidia-smi"], "returncode": 0, "fields": LIMIT_FIELDS, "raw": "700.00, 700.00, 700.00, 200.00, 700.00",
                                                "values_w": dict(zip(LIMIT_FIELDS, (700.0, 700.0, 700.0, 200.0, 700.0)))}
        edit_schedule_item(ev / "run", "1", engine, limits)
    for item_label in ("start", "end"):
        (ev / f"host-exclusivity-{item_label}.json").write_text(json.dumps({
            "format": 1, "label": item_label, "gpu_compute_apps": {"returncode": 0, "apps": []},
            "scheduled": {"systemd_timers": [], "cron_files": [], "crontab_entries": [], "at_jobs": []}}))
    (ev / "data-manifest.json").write_text(json.dumps({"format_version": 1, "scale_factor": 1, "synthetic_smoke": True}))
    captures = [json.loads((ev / f"host-exclusivity-{label}.json").read_text()) for label in ("start", "end")]
    report = pg.evaluate_round({"smoke": ev / "run"}, host_gates("h100"), captures)
    (ev / "gates.json").write_text(json.dumps(report, indent=1))
    (ev / "stages").mkdir()
    base = 1_700_000_000
    for name, start, seconds, rc_ in (("setup", 0, 640, 0), ("data", 640, 55, 0), ("run", 700, 1500, 0), ("gates", 2200, 4, 0)):
        (ev / "stages" / f"{name}-timing.json").write_text(json.dumps({"name": name, "start_utc": "x", "end_utc": "y", "start_epoch": base + start,
                                                                       "end_epoch": base + start + seconds, "seconds": seconds, "rc": rc_}))
    events = [("create_call", -300), ("ssh_ready", -60), ("setup_start", 0), ("setup_end", 645), ("run_start", 700), ("run_end", 2210), ("terminated", 2400)]
    (ev / "driver-timeline.jsonl").write_text("".join(json.dumps({"name": n, "epoch": base + t, "utc": "x"}) + "\n" for n, t in events))
    return ev, builder


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    """One clean synthetic evidence directory per module (building it is the slow part); tests mutate copies."""
    ev, builder = build_evidence(tmp_path_factory.mktemp("clean"))
    return type("Clean", (), {"ev": ev, "builder": builder})


def copy_evidence(clean, where):
    target = Path(where) / "ev"
    shutil.copytree(clean.ev, target)
    return target


@pytest.fixture(autouse=True)
def short_settle(monkeypatch):
    """The synthetic runs use the builder's 0.5 s settle; the campaign's 60 s is checked on its own below."""
    monkeypatch.setitem(cs.EXPECT, "session_idle_settle_seconds", 0.5)


def statuses(report):
    return {i["id"]: i["status"] for i in report.items}


def test_a_clean_run_has_no_fail_and_the_expected_findings(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    report = cs.check(ev)
    status = statuses(report)
    assert not report.failed, report.text()
    assert [i["id"] for i in report.items] == [f"C{n:02d}" for n in range(1, 19)]
    assert status["C09"] == "PASS" and status["C12"] == "PASS" and status["C16"] == "INFO" and status["C18"] == "INFO"
    text = report.text()
    assert "expected: the light query is meant to hit the cap" in text
    windows = next(i for i in report.items if i["id"] == "C09")["data"]["windows"]
    assert {w["stop_reason"] for w in windows} == {"max_runs", "target_reached"} and max(w["n"] for w in windows) == 50
    idle = next(i for i in report.items if i["id"] == "C08")["data"]
    assert [idle[e]["before_warmups"]["context_held"] for e in ENGINES] == [False, True, False, True]
    assert all(idle[e]["after_last_trial"]["mean_power_w"] > 0 for e in ENGINES)
    cost = next(i for i in report.items if i["id"] == "C18")
    assert any("about $0.64" in line or "about $" in line for line in cost["info"]) and cost["data"]["setup"] == 640


def test_the_command_line_writes_the_checklist_and_exits_by_the_result(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    done = subprocess.run([sys.executable, str(SMOKE / "check_smoke.py"), "check", str(ev), "--expect-idle-settle-s", "0.5"], capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (ev / "checklist.txt").read_text() == done.stdout and json.loads((ev / "checklist.json").read_text())["failed"] is False
    (ev / "run" / "gpu.csv").write_text("")
    done = subprocess.run([sys.executable, str(SMOKE / "check_smoke.py"), "check", str(ev), "--no-write", "--expect-idle-settle-s", "0.5"], capture_output=True, text=True)
    assert done.returncode == 1 and "NOT READY" in done.stdout
    count = subprocess.run([sys.executable, str(SMOKE / "check_smoke.py"), "sums", str(ev)], capture_output=True, text=True)
    assert count.returncode == 0 and (ev / "SHA256SUMS.txt").read_text().count("\n") == int(count.stdout.split()[0])


def test_the_real_campaign_idle_windows_are_30_s_after_a_60_s_settle(clean, tmp_path, monkeypatch):
    monkeypatch.setitem(cs.EXPECT, "session_idle_settle_seconds", 60.0)
    ev = copy_evidence(clean, tmp_path)
    report = cs.check(ev)
    assert statuses(report)["C08"] == "FAIL" and "campaign: 60 s" in report.text()


def only_failures(report):
    return sorted(i["id"] for i in report.items if i["status"] == "FAIL")


def test_a_gpu_field_the_driver_does_not_list_fails_the_field_check(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    edit_json(ev / "run" / "environment.json", lambda env: env["gpu_telemetry"].update(
        extra_fields=["power.draw.instant"], missing_fields=["power.draw.average", "clocks_event_reasons.active"]))
    report = cs.check(ev)
    assert "C03" in only_failures(report)
    text = " ".join(next(i for i in report.items if i["id"] == "C03")["problems"])
    assert "power.draw.average" in text and "throttle-reasons field" in text


def test_na_cells_are_counted_per_column_and_an_all_na_column_fails(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    path = ev / "run" / "gpu.csv"
    lines = path.read_text().splitlines()
    header = [c.strip() for c in lines[0].split(",")]
    col = header.index("power.draw.instant [W]")
    rows = []
    for i, line in enumerate(lines[1:]):
        cells = [c.strip() for c in line.split(",")]
        if i % 50 == 0:
            cells[col] = "[N/A]"
        rows.append(", ".join(cells))
    path.write_text("\n".join([lines[0], *rows]) + "\n")
    item = next(i for i in cs.check(ev).items if i["id"] == "C04")
    assert item["status"] == "WARN" and "power.draw.instant" in " ".join(item["warnings"]) and "[N/A]" in " ".join(item["warnings"])
    assert item["data"]["columns"]["power.draw.instant"]["n_a"] > 0
    rows = [", ".join(c if k != col else "[Not Supported]" for k, c in enumerate(x.strip() for x in line.split(","))) for line in lines[1:]]
    path.write_text("\n".join([lines[0], *rows]) + "\n")
    item = next(i for i in cs.check(ev).items if i["id"] == "C04")
    assert item["status"] == "FAIL" and "[Not Supported]" in " ".join(item["problems"])


def test_a_throttle_column_that_never_parses_fails(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    path = ev / "run" / "gpu.csv"
    lines = path.read_text().splitlines()
    rows = [line.rsplit(",", 1)[0] + ", [N/A]" for line in lines[1:]]
    path.write_text("\n".join([lines[0], *rows]) + "\n")
    item = next(i for i in cs.check(ev).items if i["id"] == "C04")
    assert item["status"] == "FAIL" and "clocks_event_reasons.active" in " ".join(item["problems"])


def test_an_enforced_limit_other_than_700_w_fails_the_power_check(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    edit_schedule_item(ev / "run", "1", "polars", lambda item: item["gpu_power_state"]["start"]["power_limits_query"]["values_w"].update({"enforced.power.limit": 350.0}))
    report = cs.check(ev)
    assert "C05" in only_failures(report) and "enforced.power.limit is 350.0" in " ".join(next(i for i in report.items if i["id"] == "C05")["problems"])
    assert "C05" not in only_failures(cs.check(copy_evidence(clean, tmp_path / "second")))


def test_a_missing_performance_capture_fails(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    (ev / "run" / "sf1" / "polars-gpu-performance-end.txt").unlink()
    assert "C06" in only_failures(cs.check(ev))


def test_a_pulse_that_did_not_run_or_has_implausible_stamps_fails(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    edit_schedule_item(ev / "run", "1", "polars", lambda item: item["gpu_pulses"]["start"].update(ok=False, error="no CUDA"))
    report = cs.check(ev)
    assert "C07" in only_failures(report) and "no CUDA" in " ".join(next(i for i in report.items if i["id"] == "C07")["problems"])
    ev2 = copy_evidence(clean, tmp_path / "second")
    edit_schedule_item(ev2 / "run", "1", "spark_cpu", lambda item: item["gpu_pulses"]["end"].update(done_monotonic_ns=item["gpu_pulses"]["end"]["launch_monotonic_ns"] + 40_000_000_000))
    problems = " ".join(next(i for i in cs.check(ev2).items if i["id"] == "C07")["problems"])
    assert "kernel span 40.000 s" in problems and "disagree" in problems


def test_a_missing_idle_window_or_a_untagged_energy_value_fails(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    edit_results(ev / "run", "1", "spark_gpu", lambda r: r["idle_windows"].pop())
    report = cs.check(ev)
    assert "C08" in only_failures(report) and "idle windows ['before_warmups']" in " ".join(next(i for i in report.items if i["id"] == "C08")["problems"])
    ev2 = copy_evidence(clean, tmp_path / "second")
    edit_results(ev2 / "run", "1", "polars", lambda r: r["records"][-1]["energy_window"]["gpu_energy"].update(scope_tag="none"))
    edit_results(ev2 / "run", "1", "polars", lambda r: r["idle_windows"][0]["gpu_energy"].update(scope_tag="S1-module"))
    report2 = cs.check(ev2)
    assert {"C08", "C09", "C10"} <= set(only_failures(report2))


def test_a_cpu_engine_that_holds_a_cuda_context_is_called_out(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    builder = type("B", (), {"context_open_ranges": [(0.0, 4e9)]})()          # every moment of the run now has a context
    rewrite_memory(ev / "run" / "gpu.csv", builder)
    item = next(i for i in cs.check(ev).items if i["id"] == "C08")
    assert item["status"] == "WARN" and "never fell below 611 MiB" in " ".join(item["warnings"])
    assert item["data"]["polars"]["before_warmups"]["context_held"] is None
    held_cpu = copy_evidence(clean, tmp_path / "b")                    # a CPU engine's session alone with a context
    lines = (held_cpu / "run" / "gpu.csv").read_text().splitlines()
    edit_results(held_cpu / "run", "1", "polars", lambda r: None)
    first_window = json.loads((held_cpu / "run" / "sf1" / "polars" / "results.json").read_text())["idle_windows"][0]
    start, end = (datetime.fromisoformat(first_window[k]).timestamp() for k in ("start_utc", "end_utc"))
    rows = [lines[0]]
    for line in lines[1:]:
        cells = [c.strip() for c in line.split(",")]
        t = datetime.strptime(cells[0], "%Y/%m/%d %H:%M:%S.%f").replace(tzinfo=timezone.utc).timestamp()
        if start <= t <= end:
            cells[4] = "611 MiB"
        rows.append(", ".join(cells))
    (held_cpu / "run" / "gpu.csv").write_text("\n".join(rows) + "\n")
    item = next(i for i in cs.check(held_cpu).items if i["id"] == "C08")
    assert item["status"] == "WARN" and "polars: holds a CUDA context in the first idle window" in " ".join(item["warnings"])


def test_windows_that_never_reach_either_regime_are_warned_about(clean, tmp_path):
    only_light = copy_evidence(clean, tmp_path / "a")
    edit_results(only_light / "run", "1", "polars", lambda r: r.update(records=[x for x in r["records"] if x["query"] != "sm_light"]))
    for engine in ENGINES[1:]:
        edit_results(only_light / "run", "1", engine, lambda r: r.update(records=[x for x in r["records"] if x["query"] != "sm_light"]))
    item = next(i for i in cs.check(only_light).items if i["id"] == "C09")
    assert item["status"] == "WARN" and "no window stopped at the 50-run cap" in " ".join(item["warnings"])
    capped_only = copy_evidence(clean, tmp_path / "b")
    for engine in ENGINES:
        edit_results(capped_only / "run", "1", engine, lambda r: r.update(records=[x for x in r["records"] if x["query"] == "sm_light" or x["phase"] == "warmup"]))
    item = next(i for i in cs.check(capped_only).items if i["id"] == "C09")
    assert item["status"] == "WARN" and "no window stopped by reaching the 10 s target" in " ".join(item["warnings"])


def test_a_broken_window_rule_or_disagreeing_run_fails(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)

    def break_it(results):
        window = next(r for r in results["records"] if r["phase"] == "measured" and r["query"] == "sm_join")["energy_window"]
        window["stop_reason"] = "max_runs"
        window["runs"][1]["output_check"] = {"rows": 3, "checked_against": "window run 1", "agrees": False, "mismatch_count": 1}
    edit_results(ev / "run", "1", "polars", break_it)
    problems = " ".join(next(i for i in cs.check(ev).items if i["id"] == "C09")["problems"])
    assert "stop_reason" in problems and "different rows than run 1" in problems


def test_chrony_must_be_logged_at_both_ends_of_every_session(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    edit_schedule_item(ev / "run", "1", "spark_cpu", lambda item: item["clock_sync"]["end"].update(returncode=127, system_time_offset_seconds=None))
    assert "C11" in only_failures(cs.check(ev))


def test_a_failed_gate_a_missing_report_or_an_unexpected_cell_fails_the_gates_item(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    report = json.loads((ev / "gates.json").read_text())
    report["runs"][0]["sessions"][1]["gates"]["G7"] = {"outcome": "fail", "problems": ["coverage 0.5"]}
    (ev / "gates.json").write_text(json.dumps(report))
    assert "C12" in only_failures(cs.check(ev))
    (ev / "gates.json").unlink()
    item = next(i for i in cs.check(ev).items if i["id"] == "C12")
    assert item["status"] == "FAIL" and "gates.json missing" in item["problems"][0]
    ev2 = copy_evidence(clean, tmp_path / "second")
    report = json.loads((ev2 / "gates.json").read_text())
    cell = next(c for c in report["runs"][0]["cells"] if c["query"] == "sm_join")
    cell.update(outcome="fail", reasons=["per_run_below_t_min"])
    (ev2 / "gates.json").write_text(json.dumps(report))
    item = next(i for i in cs.check(ev2).items if i["id"] == "C12")
    assert item["status"] == "WARN" and "enlarge --facts-rows" in " ".join(item["warnings"])


def test_a_sparse_poller_fails_the_coverage_rule(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    lines = (ev / "run" / "gpu.csv").read_text().splitlines()
    (ev / "run" / "gpu.csv").write_text("\n".join([lines[0], *lines[1::3]]) + "\n")           # every third row: ~600 ms spacing
    item = next(i for i in cs.check(ev).items if i["id"] == "C13")
    assert item["status"] == "FAIL" and "median spacing" in " ".join(item["problems"])


def test_engines_that_disagree_on_a_result_fail_the_output_check(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)

    def wrong(results):
        next(r for r in results["records"] if r["phase"] == "warmup" and r["query"] == "sm_scan")["rows"] = [{"k": 1, "total": 99.0, "n": 7}]
    edit_results(ev / "run", "1", "spark_cpu", wrong)
    problems = " ".join(next(i for i in cs.check(ev).items if i["id"] == "C14")["problems"])
    assert "sm_scan: spark_cpu differs from polars" in problems


def test_a_foreign_gpu_process_fails_the_exclusivity_item(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    edit_json(ev / "host-exclusivity-end.json", lambda c: c["gpu_compute_apps"].update(apps=["999, python, 400"]))
    assert "C15" in only_failures(cs.check(ev))


def test_a_failed_engine_is_one_fail_with_its_message(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)

    def failed(results):
        results.update(query_execution_status="partial", query_errors=[{"query": "sm_join", "phase": "warmup", "trial": 0, "type": "RuntimeError",
                                                                          "message": "cudf-polars: unsupported"}])
    edit_results(ev / "run", "1", "polars_gpu", failed)
    problems = " ".join(next(i for i in cs.check(ev).items if i["id"] == "C02")["problems"])
    assert "polars_gpu" in problems and "cudf-polars: unsupported" in problems


def test_a_missing_run_directory_is_reported_not_raised(tmp_path):
    (tmp_path / "ev").mkdir()
    report = cs.check(tmp_path / "ev")
    assert report.failed and statuses(report)["C00"] == "FAIL"


# ---- redaction ---------------------------------------------------------------------------------------------------------------

LEAKY = """Driver Version : 570.148.08
    GPU UUID                              : GPU-0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0
    Serial Number                         : 1654321098765
    Bus Id                                : 00000000:CB:00.0
Starting executor ID driver on host 10.19.4.77 (172.26.132.191)
host 192-222-51-76 and ip-10-19-4-77 and chrony 169.254.169.123 loopback 127.0.0.1 versions cuda-toolkit==12.9.2.0 jdk 17.0.12
the instance p3-probe-h100-smoke-owner-ejiod-expires-20261116T0727Z
"""


def test_publish_removes_identifiers_and_keeps_versions(tmp_path):
    bundle, evidence = tmp_path / "bundle", tmp_path / "evidence"
    (bundle / "run").mkdir(parents=True)
    (bundle / "run" / "spark.log").write_text(LEAKY)
    (bundle / "run" / "data.bin").write_bytes(b"\x00\x01GPU-0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0")
    result = cs.publish(bundle, evidence, ["192-222-51-76", "p3-probe-h100-smoke-owner-ejiod-expires-20261116T0727Z", ""])
    text = (evidence / "run" / "spark.log").read_text()
    for gone in ("0f1e2d3c", "1654321098765", "00000000:CB:00.0", "10.19.4.77", "172.26.132.191", "192-222-51-76", "ip-10-19-4-77", "owner-ejiod"):
        assert gone not in text, gone
    for kept in ("570.148.08", "169.254.169.123", "127.0.0.1", "cuda-toolkit==12.9.2.0", "17.0.12"):
        assert kept in text, kept
    assert result["removed"]["ip address"] == 2 and result["removed"]["gpu uuid"] == 1 and (evidence / "run" / "data.bin").read_bytes().startswith(b"\x00\x01GPU-")
    assert json.loads((evidence / "redaction.json").read_text())["files"] == 2
    assert cs.scan_redaction(evidence) == []
    (evidence / "run" / "leak.txt").write_text("Bus Id : 00000000:AF:00.0\nhost 10.1.2.3\n")
    assert sorted(rule for _, rule in cs.scan_redaction(evidence)) == ["bus id x1", "ip address x1"]


def test_the_checklist_fails_an_evidence_directory_that_still_has_an_identifier(clean, tmp_path):
    ev = copy_evidence(clean, tmp_path)
    (ev / "stages").joinpath("setup.log").write_text("connected from 10.19.4.77\n")
    report = cs.check(ev)
    assert "C17" in only_failures(report) and "setup.log: ip address x1" in " ".join(next(i for i in report.items if i["id"] == "C17")["problems"])


# ---- the H100 driver against fakes ---------------------------------------------------------------------------------------------
# The fake host runs smoke_host.sh (the real one), the real generator, smoke_command.py, paper3_gates.py and check_smoke.py; only
# setup_h100.sh, run_experiment.py (it lays down a prebuilt synthetic run), lambda.sh, ssh/scp and nvidia-smi are stand-ins.

from test_paper3_probe_drivers import BASH, net  # noqa: E402,F401  (the fake ssh/scp host fixture)

STUB_SETUP = """#!/usr/bin/env bash
mkdir -p rerun
printf 'export PYTHON_GPU_ENV="%s"\\nexport DATA_ROOT="$HOME/data"\\nexport RAPIDS_JAR="$HOME/rapids.jar"\\n' "$FAKE_PYTHON" > rerun/h100-env.sh
sleep ${FAKE_SETUP_SLEEP:-0.3}
exit ${FAKE_SETUP_RC:-0}
"""
STUB_RUN_EXPERIMENT = """import os, shutil, sys
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--output") + 1])
Path("argv-run.txt").write_text(" ".join(sys.argv[1:]))
shutil.copytree(os.environ["FAKE_PREBUILT_RUN"], out)
print('{"event": "engine_complete"}')
"""
FAKE_SMOKE_LAMBDA = """#!/bin/bash
state=${P3_STATE_DIR:-/tmp/p3probe}
mkdir -p "$state"
case "$1" in
  launch) echo "$FAKE_INSTANCE_ID"; echo "p3-probe-$2-owner-ejiod-expires-fake" > "$state/$FAKE_INSTANCE_ID.name"; echo "$@" > "$FAKE_NET/launch_args"; echo launched >&2 ;;
  wait) echo 10.0.0.1 > "$state/$2.ip"; echo "ssh ready" ;;
  list) [ -f "$FAKE_NET/terminated" ] || printf '%s\\tp3-probe\\tactive\\tgpu\\tregion\\n' "$FAKE_INSTANCE_ID" ;;
  terminate) touch "$FAKE_NET/terminated"; echo "[]" ;;
esac
"""


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), "-c", "user.email=t@example.org", "-c", "user.name=t", *args], capture_output=True, text=True, check=True)


@pytest.fixture
def smoke_repo(net, clean):
    """A throwaway repository holding the real smoke tooling and real gates, committed, with stand-ins for what needs a GPU host."""
    root = net.tmp / "srepo"
    (root / "tools" / "paper3_probes").mkdir(parents=True)
    for source in REPO.glob("*.py"):
        shutil.copy(source, root / source.name)
    shutil.copy(REPO / "paper3_hosts.json", root / "paper3_hosts.json")
    shutil.copytree(SMOKE, root / "tools" / "paper3_smoke", ignore=shutil.ignore_patterns("__pycache__", "run-*.log"))
    for name in ("p3_stage_lib.sh", "p3_detach.sh"):
        shutil.copy(REPO / "tools" / "paper3_probes" / name, root / "tools" / "paper3_probes" / name)
    (root / "tools" / "paper3_probes" / "lambda.sh").write_text(FAKE_SMOKE_LAMBDA)
    (root / "tools" / "paper3_probes" / "lambda_watchdog.sh").write_text("#!/bin/bash\nsleep 0.1\n")
    (root / "setup_h100.sh").write_text(STUB_SETUP)
    (root / "run_experiment.py").write_text(STUB_RUN_EXPERIMENT)
    for path in list((root / "tools").rglob("*.sh")) + [root / "setup_h100.sh"]:
        path.chmod(0o755)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "fixture")
    fake_bin = net.bin
    (fake_bin / "nvidia-smi").write_text("#!/bin/bash\nexit 0\n")
    (fake_bin / "hostname").write_text("#!/bin/bash\necho ip-10-1-2-3\n")
    for name in ("nvidia-smi", "hostname"):
        (fake_bin / name).chmod(0o755)
    instance = f"smoke{os.getpid()}"
    env = {**net.env, "FAKE_INSTANCE_ID": instance, "FAKE_PYTHON": sys.executable, "FAKE_PREBUILT_RUN": str(clean.ev / "run"), "TMPDIR": str(net.tmp),
           "P3_STATE_DIR": str(net.tmp / "state"), "P3_SMOKE_PYTHON": sys.executable, "P3_SMOKE_EXPECT_SETTLE": "0.5", "P3_POLL_SECONDS": "0.3",
           "P3_SMOKE_DATA_ARGS": "--small-rows 200 --facts-rows 2000 --dims-rows 50 --facts-parts 2"}
    return type("Smoke", (), {"root": root, "env": env, "evidence": root / "docs" / "paper3-evidence" / "smoke"})


def run_smoke_driver(repo, *args, timeout=240, **extra):
    return subprocess.run([BASH, str(repo.root / "tools" / "paper3_smoke" / "run_h100_smoke.sh"), "gpu_1x_h100_sxm5", "region-1", "2", "60", *args],
                          capture_output=True, text=True, timeout=timeout, env={**repo.env, **{k: str(v) for k, v in extra.items()}})


def test_the_driver_runs_every_stage_collects_redacts_checks_and_terminates(net, smoke_repo):
    done = run_smoke_driver(smoke_repo)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]
    (evidence,) = [p for p in smoke_repo.evidence.iterdir() if p.is_dir()]
    assert evidence.name.startswith("h100-")
    log = (evidence / "driver.log").read_text()
    for stage in ("setup", "data", "run", "gates", "bundle"):
        assert f"stage {stage} exit=0" in log
    assert "confirmed terminated" in log and (net.net / "terminated").exists() and "10.0.0.1" not in log
    assert "p3-probe-h100-smoke" in (net.net / "launch_args").read_text() or "h100-smoke" in (net.net / "launch_args").read_text()
    for name in ("run/schedule.json", "run/environment.json", "run/gpu.csv", "run/sf1/polars/results.json", "gates.json", "data-manifest.json",
                 "host-exclusivity-start.json", "host-exclusivity-end.json", "stages/setup-timing.json", "stages/run-timing.json", "h100-env.sh",
                 "driver-timeline.jsonl", "redaction.json", "checklist.txt", "checklist.json", "SHA256SUMS.txt"):
        assert (evidence / name).is_file(), name
    assert not list((evidence / "run").rglob("*-events")), "Spark event logs are not collected"
    checklist = json.loads((evidence / "checklist.json").read_text())
    assert checklist["failed"] is False and [i["id"] for i in checklist["items"]] == [f"C{n:02d}" for n in range(1, 19)], (evidence / "checklist.txt").read_text()
    names = [json.loads(line)["name"] for line in (evidence / "driver-timeline.jsonl").read_text().splitlines()]
    assert names[:2] == ["create_call", "launched"] and {"ssh_ready", "repo_copied", "setup_start", "setup_end", "run_start", "run_end", "fetched",
                                                           "terminate_call", "terminated"} <= set(names)
    manifest = json.loads((evidence / "data-manifest.json").read_text())
    assert manifest["synthetic_smoke"] is True and manifest["tables"]["facts"]["rows"] == 2000      # the real generator ran on the host
    command = (net.host / "gpu-spark-polars-study" / "argv-run.txt").read_text()
    assert "--suite smoke" in command and command.endswith("--synthetic-smoke") and "--gpu-energy-scope board" in command and "--energy-window-s 10.0" in command
    sums = (evidence / "SHA256SUMS.txt").read_text().splitlines()
    assert any(line.split()[1] == "checklist.json" for line in sums) and all(line.split()[1] != "SHA256SUMS.txt" for line in sums)
    for line in sums[:5]:
        digest, name = line.split()[:2]
        assert subprocess.run(["shasum", "-a", "256", str(evidence / name)], capture_output=True, text=True).stdout.split()[0] == digest
    for path in evidence.rglob("*"):
        if path.is_file() and path.suffix in cs.TEXT_SUFFIXES:
            assert "ip-10-1-2-3" not in path.read_text(), path


def test_a_failed_setup_skips_the_run_but_still_collects_and_terminates(net, smoke_repo):
    done = run_smoke_driver(smoke_repo, FAKE_SETUP_RC=3)
    assert done.returncode == 1, done.stdout[-2000:]
    (evidence,) = [p for p in smoke_repo.evidence.iterdir() if p.is_dir()]
    log = (evidence / "driver.log").read_text()
    assert "stage setup exit=3" in log and "the run and the gates are skipped" in log and "stage run exit" not in log
    assert "confirmed terminated" in log and (net.net / "terminated").exists()
    assert json.loads((evidence / "stages" / "setup-timing.json").read_text())["rc"] == 3
    checklist = json.loads((evidence / "checklist.json").read_text())
    assert checklist["failed"] is True and checklist["items"][0]["id"] == "C00"


def test_the_driver_refuses_a_head_without_the_smoke_files_before_launching_anything(net, smoke_repo):
    git(smoke_repo.root, "rm", "-q", "smoke_workloads.py")
    git(smoke_repo.root, "commit", "-q", "-m", "drop")
    done = run_smoke_driver(smoke_repo)
    assert done.returncode == 1 and "lacks smoke_workloads.py" in done.stdout
    assert not (net.net / "launch_args").exists() and not (net.net / "terminated").exists()
    worktree = run_smoke_driver(smoke_repo, P3_SMOKE_SOURCE="worktree")
    assert worktree.returncode == 1 and "smoke_workloads.py is missing from the working tree" in worktree.stdout


def test_a_signal_to_the_driver_still_terminates_the_instance(net, smoke_repo):
    import signal
    import time
    process = subprocess.Popen([BASH, str(smoke_repo.root / "tools" / "paper3_smoke" / "run_h100_smoke.sh"), "gpu_1x_h100_sxm5", "region-1", "2", "60"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**smoke_repo.env, "FAKE_SETUP_SLEEP": "60"})
    deadline = time.time() + 60
    while time.time() < deadline and not (net.host / "p3out" / "setup.started").exists():
        time.sleep(0.2)
    assert (net.host / "p3out" / "setup.started").exists(), "the setup stage never started"
    process.send_signal(signal.SIGTERM)
    output, _ = process.communicate(timeout=120)
    assert process.returncode == 143, output[-2000:]
    assert (net.net / "terminated").exists() and "confirmed terminated" in output and "terminated (signal)" in output
