"""Paper 3 runbook tooling (tools/paper3_campaign/, docs/PAPER3_RUNBOOK.md): the planner, the QA view, the host-side pack script, and the
generated blocks of the runbook. The operator scripts (launcher, driver, teardown, watchdog) are in test_paper3_runbook_ops.py.

Offline: nothing here launches anything or reads a credential. The planner is checked against the locked design's own text (prices,
hours, the ~$1,364 core estimate), against the repository's schedule (paper3_campaign.py) and against run_campaign.py itself (every
generated command line is parsed and dry-run by the real CLI).
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "paper3_campaign"
for path in (str(REPO), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import p3_plan as P  # noqa: E402
import p3_qa_view as Q  # noqa: E402
import paper3_campaign as pc  # noqa: E402
import probe_power  # noqa: E402

BASH = shutil.which("bash")
DESIGN = (REPO / "docs" / "PAPER3_ENERGY_DESIGN.md").read_text()
NOW = datetime(2026, 11, 17, 6, 0, tzinfo=timezone.utc)
HOSTS = list(P.PLANS)
LAUNCHES = [(h, l) for h in HOSTS for l in ("1", "2") if not (l == "2" and P.PLANS[h].launches == 1)]


# ---- the planner against the design's own text ---------------------------------------------------------------------------------

def test_prices_and_instance_types_are_the_designs_p_new_table():
    rows = dict(re.findall(r"\| (G-A100|G-H100|G-GH200|G-B200) \| Lambda `(\w+)`", DESIGN))
    assert len(rows) == 4
    for key, host in (("G-A100", "a100"), ("G-H100", "h100"), ("G-GH200", "gh200"), ("G-B200", "b200")):
        assert P.PLANS[host].instance_type == rows[key] == pc.HOSTS[host].instance_types[0]
    prices = {m.group(1): float(m.group(2)) for m in re.finditer(r"\| (G-A100|G-H100|G-GH200|G-B200|C-GCP|C-VM) \|.*?\| ([\d.]+) \((?:[\d.]+|same)\) \|\s*$", DESIGN, re.M)}
    assert prices == {"G-A100": 1.99, "G-H100": 4.89, "G-GH200": 2.59, "G-B200": 7.99, "C-GCP": 12.701568, "C-VM": 2.910776}
    for key, host in (("G-A100", "a100"), ("G-H100", "h100"), ("G-GH200", "gh200"), ("G-B200", "b200"), ("C-GCP", "c-gcp"), ("C-VM", "c-vm")):
        assert P.PLANS[host].price == prices[key]
    assert P.PLANS["c-gcp"].instance_type == pc.HOSTS["c-gcp"].instance_types[0] and P.PLANS["c-vm"].instance_type == pc.HOSTS["c-vm"].instance_types[0]


def test_hours_are_the_designs_budget_rows():
    row = re.search(r"\| G-A100 / G-H100 / G-GH200 / G-B200 \| ([\d. /]+) \| ([\d. /]+) \| ([\d. /]+) \|", DESIGN)
    hours = [float(x) for x in row.group(1).split("/")]
    assert hours == [P.PLANS[h].base_h for h in ("a100", "h100", "gh200", "b200")]
    assert [float(x) for x in row.group(2).split("/")] == [P.PLANS[h].price for h in ("a100", "h100", "gh200", "b200")]
    assert re.search(r"\(23\.0 h each\) \| 92\.2 \| 17\.46 summed \| 402 \|", DESIGN)
    assert {P.PLANS[h].overhead_h for h in ("a100", "h100", "gh200", "b200")} == {23.0}
    assert re.search(r"Remaining probes and validation P5-P7: 1\.5 h x 4 GPU host classes \| 6\.0 \| 17\.46 summed \| 26 \|", DESIGN)
    assert {P.PLANS[h].probe_h for h in ("a100", "h100", "gh200", "b200")} == {1.5}
    gcp = P.PLANS["c-gcp"]
    assert re.search(r"C-GCP base run \(20 h\); overhead and probes P5, P6 \(13\.5 h incl\. boot\) \| 33\.5 \| 12\.701568 \| 425 \|", DESIGN)
    assert (gcp.base_h, gcp.overhead_h + gcp.probe_h) == (20.0, 13.5)
    assert re.search(r"\| 6 \| 2\.910776 \| 17 \|", DESIGN) and (P.PLANS["c-vm"].base_h, P.PLANS["c-vm"].launches) == (6.0, 1)
    assert re.search(r"Storage, snapshots, egress .*\| 50 \|", DESIGN) and P.STORAGE_EGRESS_USD == 50.0


def test_the_core_total_is_the_designs_1364_and_the_subtotals_match():
    table = P.cost_rows()
    assert abs(table["computed_core_total_usd"] - 1364.0) < 1.0
    assert abs(table["lambda_subtotal_usd"] - 871.0) < 1.0 and abs(table["gcp_subtotal_usd"] - 443.0) < 1.0
    assert "**~1,364**" in DESIGN and table["design_core_total_usd"] == 1364.0
    for row in table["rows"]:                                    # each host's rows are hours times price; probes are separate
        plan = P.PLANS[row["host"]]
        assert math.isclose(row["campaign_usd"], (plan.base_h + plan.overhead_h) * plan.price, abs_tol=0.01)


def test_per_launch_hours_follow_the_designs_formula_and_the_lease_covers_one_extra_round():
    for host, expected in (("a100", 27.0), ("h100", 24.4), ("gh200", 21.45), ("b200", 24.25), ("c-gcp", 15.75), ("c-vm", 6.0)):
        assert math.isclose(P.PLANS[host].launch_h, expected, abs_tol=0.01), host
    assert (P.PAUSE_H, P.LEASE_MARGIN_H) == (0.12, 1.0)
    for plan in P.PLANS.values():
        assert plan.lease_h * 2 == int(plan.lease_h * 2)                                 # half-hour steps
        assert plan.lease_h >= plan.launch_h + plan.rounds * 0.12 + plan.round_h + 1.0 - 1e-9     # the estimate, the pauses, one more round, an hour
    paper1 = {"a100": 11 + 24 / 60, "h100": 9 + 15 / 60, "gh200": 6 + 47 / 60, "b200": 9 + 7 / 60}       # REPRODUCE.md, side study included
    for host, hours in paper1.items():                                                   # the design's formula (section 9)
        assert math.isclose(P.PLANS[host].base_h, (2 * hours + 3) * 1.2, abs_tol=0.06), host


def test_the_timeline_respects_the_quota_and_ends_where_the_runbook_says():
    t = P.timeline()
    rows = {r["host"]: r for r in t["rows"]}
    assert rows["c-gcp"]["launch2_start_h"] > rows["c-gcp"]["launch1_end_h"]              # C3_CPUS 300 per region: one 192-vCPU host at a time
    assert rows["c-vm"]["launch1_start_h"] >= t["pathfinder_gate_hours"] and "launch2_start_h" not in rows["c-vm"]
    for host in ("a100", "gh200"):
        assert rows[host]["launch1_start_h"] == t["pathfinder_gate_hours"] and rows[host]["launch2_start_h"] > rows[host]["launch1_end_h"]
    assert t["all_done_hours"] == max(r.get("launch2_end_h", r["launch1_end_h"]) for r in t["rows"])
    assert 2.0 < t["all_done_days"] < 3.5
    assert 192 + 44 <= 300                                                                # C-GCP and C-VM fit one region's C3_CPUS quota together
    assert 2 * 192 > 300


def test_input_sizes_are_the_committed_manifests():
    sources = {"1": "production-20261025/sf1.json", "10": "production-20261025/sf10.json", "30": "production-20261025/sf30.json",
               "100": "large-sf100-20261025/sf100.json", "300": "large-sf300-20261025/sf300.json"}
    for scale, relative in sources.items():
        manifest = json.loads((REPO / "evidence" / "INPUT_MANIFESTS" / relative).read_text())
        assert sum(t["parquet_bytes"] for t in manifest["tables"].values()) == P.INPUT_BYTES[scale]
        assert math.isclose(manifest["generation_seconds"], P.INPUT_GEN_SECONDS[scale], abs_tol=0.06)
    assert round(sum(P.INPUT_BYTES.values()) / 2**30, 1) == P.input_table()["total_gib"] == 147.1


def test_the_lock_constants_are_the_repositorys_lock():
    done = subprocess.run(["git", "-C", str(REPO), "rev-parse", f"refs/tags/{P.LOCK['tag']}^{{commit}}"], capture_output=True, text=True)
    if done.returncode:
        pytest.skip("no paper3-design-lock tag in this checkout")
    assert done.stdout.strip() == P.LOCK["commit"]
    assert P.LOCK["doi"] == "10.5281/zenodo.24284712" and P.LOCK["date"] == "20261116"
    assert pc.build_plan("h100", P.LOCK["date"]).seed == 20261116


def test_the_lock_evidence_the_hosts_will_check_holds_on_this_checkout():
    if subprocess.run(["git", "-C", str(REPO), "rev-parse", f"refs/tags/{P.LOCK['tag']}"], capture_output=True).returncode:
        pytest.skip("no paper3-design-lock tag in this checkout")
    evidence = P.check_lock()                                     # the tag, its date = the seed, LOCKED status line, sections 1-11 byte for byte
    assert evidence["commit"] == P.LOCK["commit"] and evidence["sections_1_11_unchanged"] and evidence["tagger_date_utc"].startswith("2026-11-16")


# ---- leases and launch requests ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("host, launch", LAUNCHES)
def test_every_lease_name_satisfies_the_launch_convention_and_the_providers_rules(host, launch):
    spec = P.lease_spec(host, launch, NOW)
    name, plan = spec["lease_name"], P.PLANS[host]
    status = probe_power.lease_status(name, now=NOW)
    assert status["valid"] and status["owner"] == "ejiod" and status["purpose"] == f"campaign-{plan.token}-l{launch}"
    expires = datetime.strptime(spec["expires_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert (expires - NOW).total_seconds() == plan.lease_h * 3600 and status["minutes_left"] == plan.lease_h * 60
    if plan.provider == "gcp":
        assert name == name.lower() and len(name) <= 63 and re.fullmatch(r"[a-z]([-a-z0-9]*[a-z0-9])?", name)
    else:
        assert name.endswith("Z") and "T" in name.rsplit("-", 1)[1]


def test_lambda_requests_carry_the_lease_as_the_instance_name_and_the_probe_key():
    spec = P.launch_spec("h100", "1", "us-south-2", NOW)
    assert spec["body"] == {"region_name": "us-south-2", "instance_type_name": "gpu_1x_h100_sxm5", "ssh_key_names": ["Key22"],
                            "file_system_names": [], "quantity": 1, "name": spec["lease_name"]}
    assert spec["credit_funded_default"] == "yes"
    with pytest.raises(P.PlanError):
        P.launch_spec("h100", "1", "Moon Base", NOW)


def test_gcp_requests_have_the_provider_side_limit_the_labels_and_the_fixed_disk():
    spec = P.launch_spec("c-gcp", "1", "us-east4-c", NOW)
    command = spec["command"]
    assert command[:4] == ["gcloud", "compute", "instances", "create"] and command[4] == spec["lease_name"]
    assert "--instance-termination-action=DELETE" in command and "--maintenance-policy=TERMINATE" in command
    assert f"--max-run-duration={spec['max_run_duration']}" in command and spec["max_run_duration"] == "21h"
    assert "--machine-type=c3-highmem-192-metal" in command and "--boot-disk-type=hyperdisk-balanced" in command
    assert "--boot-disk-size=800GB" in command and "--boot-disk-provisioned-iops=16000" in command and "--boot-disk-provisioned-throughput=1000" in command
    labels = dict(kv.split("=") for kv in next(c for c in command if c.startswith("--labels=")).split("=", 1)[1].split(","))
    assert labels["project"] == "paper3-energy" and labels["purpose"] == "campaign" and labels["expires"] == spec["lease_name"].rsplit("-", 1)[1]
    assert all(v == v.lower() for v in labels.values())
    assert spec["credit_funded_default"] is None                                         # the author says whether GCP is credit-funded
    vm = P.launch_spec("c-vm", "1", "us-east4-a", NOW)
    assert "--machine-type=c3-highmem-44" in vm["command"] and vm["max_run_duration"] == "10h"
    assert [c for c in vm["command"] if c.startswith("--boot-disk")] == [c for c in command if c.startswith("--boot-disk")]      # like-for-like storage


@pytest.mark.parametrize("zone", ["us-west1-a", "us-east1-b", "europe-west4-a", "us-east4", "us-east4-z"])
def test_gcp_zones_outside_the_frozen_price_table_are_refused(zone):
    with pytest.raises(P.PlanError, match="frozen P-NEW GCP table"):
        P.launch_spec("c-gcp", "1", zone, NOW)


def test_only_the_designs_instance_types_can_be_launched_and_c_vm_has_one_launch():
    assert P.launch_spec("c-gcp", "1", "us-east4-c", NOW, instance_type="c3-standard-192-metal")["instance_type"] == "c3-standard-192-metal"
    with pytest.raises(P.PlanError):
        P.launch_spec("c-gcp", "1", "us-east4-c", NOW, instance_type="n2-standard-8")
    with pytest.raises(P.PlanError):
        P.launch_spec("h100", "1", "us-south-2", NOW, instance_type="gpu_1x_h100_pcie")
    with pytest.raises(P.PlanError):
        P.check_launch("c-vm", "2")


def test_b200_is_admitted_by_its_t_min_in_the_host_config_and_refused_without_it(tmp_path):
    P.check_admitted("b200")                                                              # amendment A2: t_min_s 1.0 is in the shipped config
    assert P.launch_spec("b200", "1", "australia-east-1", NOW)["instance_type"] == "gpu_1x_b200_sxm6"
    config = json.loads((REPO / "paper3_hosts.json").read_text())
    config["hosts"]["b200"]["t_min_s"] = None
    path = tmp_path / "hosts.json"
    path.write_text(json.dumps(config))
    with pytest.raises(P.PlanError, match="not admitted"):
        P.check_admitted("b200", path)                                                    # the refusal still works for a config without a t_min


# ---- the campaign command, parsed and dry-run by the real CLI -------------------------------------------------------------------

def record_for(host, launch, **extra):
    spec = P.lease_spec(host, launch, NOW)
    plan = P.PLANS[host]
    return {"host": host, "launch": int(launch), "provider": plan.provider, "instance_type": plan.instance_type,
            "region": None if plan.provider == "gcp" else plan.places[0], "zone": plan.places[0] if plan.provider == "gcp" else None,
            "instance_id": "i-0123", "lease_name": spec["lease_name"], "launch_utc": "2026-11-17T06:01:00Z", "price_usd_h": plan.price,
            "price_snapshot_utc": "2026-11-17T05:40:00Z", "credit_funded": "yes", "campaign_commit": "a" * 40, **extra}


def shell_words(text):
    import shlex
    return shlex.split(text.replace("\\\n", " "))


@pytest.mark.parametrize("host, launch", LAUNCHES)
def test_the_campaign_command_is_accepted_by_run_campaign_and_runs_the_launchs_rounds(host, launch):
    record = record_for(host, launch)
    words = shell_words(P.words(P.campaign_args(host, launch, record)))
    assert words[:2] == ["python", "run_campaign.py"]
    words = [("/data" if w == "$DATA_ROOT" else "/j.jar" if w == "$RAPIDS_JAR" else w) for w in words[2:]]
    done = subprocess.run([sys.executable, str(REPO / "run_campaign.py"), "--dry-run", *words], capture_output=True, text=True, timeout=120, cwd=REPO)
    assert done.returncode == 0, done.stderr
    rounds = P.rounds_of(host, launch)
    assert f"THIS LAUNCH: rounds {rounds}" in done.stdout
    assert f"host {host} " in done.stdout and "formal: True" in done.stdout
    assert ("host config unset: ['allowed_scheduled']" in done.stdout) == (host == "c-gcp")      # filled by the reviewed allow-list, nothing else is pending
    if host == "b200":
        assert "t_min: 1.0" in done.stdout                                                # admitted by amendment A2


def test_the_campaign_command_has_the_lock_cost_and_lease_flags_the_runbook_promises():
    record = record_for("h100", "2")
    args = P.campaign_args("h100", "2", record, stop_after=6)
    flat = [str(a) for a in args]

    def value(flag):
        return flat[flat.index(flag) + 1]
    assert value("--paper3-host") == "h100" and value("--paper3-launch") == "2" and value("--paper3-lock-date") == "20261116"
    assert value("--paper3-lock-commit") == P.LOCK["commit"] and value("--paper3-lock-doi") == "10.5281/zenodo.24284712"
    assert value("--lease-name") == record["lease_name"] and value("--stop-after-round") == "6" and value("--root") == "rerun/paper3-h100-l2"
    assert value("--host-label") == "gpu_1x_h100_sxm5" and value("--cost-provider") == "lambda-labs" and value("--cost-region") == "us-south-2"
    assert value("--cost-instance-id") == "i-0123" and value("--cost-launch-utc") == "2026-11-17T06:01:00Z" and value("--cost-credit-funded") == "yes"
    assert value("--cost-hourly-price-usd") == "4.89" and value("--cost-price-snapshot-utc") == "2026-11-17T05:40:00Z"
    assert value("--engine-timeout-seconds") == "7200" and "--no-lease-check" not in flat and "--rounds" not in flat
    assert int(value("--lease-min-minutes")) == math.ceil((P.PLANS["h100"].round_h + 0.5) * 60)
    gcp = [str(a) for a in P.campaign_args("c-gcp", "1", record_for("c-gcp", "1", credit_funded="no"))]
    assert gcp[gcp.index("--cpu-list") + 1] == "0-12,96-108" and gcp[gcp.index("--cost-region") + 1] == "us-east4-a"
    assert gcp[gcp.index("--cost-provider") + 1] == "gcp" and gcp[gcp.index("--cost-credit-funded") + 1] == "no"
    assert gcp[gcp.index("--jar") + 1].endswith("not-used-on-a-cpu-host.jar")
    vm = [str(a) for a in P.campaign_args("c-vm", "1", record_for("c-vm", "1"))]
    assert "--cpu-list" not in vm and "rerun/verify-sf300.json" in vm and "rerun/verify-sf100-300.json" not in vm


def test_without_a_record_the_command_shows_placeholders_never_invented_values():
    text = P.words(P.campaign_args("a100", "1"))
    for placeholder in ("<LEASE_NAME>", "<INSTANCE_ID>", "<LAUNCH_UTC>", "<REGION_OR_ZONE>", "<PRICE_USD_PER_H>", "<PRICE_SNAPSHOT_UTC>"):
        assert shell_words(text).count(placeholder) == 1, placeholder
    assert "$DATA_ROOT" in text and "$RAPIDS_JAR" in text and "'$DATA_ROOT'" not in text     # expanded by the host's shell, not quoted away
    assert "<yes|no>" in P.words(P.campaign_args("c-gcp", "1"))                              # GCP has no default: the author says


def test_the_last_round_has_no_stop_flag_and_the_others_do():
    for host, launch in LAUNCHES:
        rounds = P.rounds_of(host, launch)
        assert rounds == ([1, 2] if host == "c-vm" else [1, 2, 3, 4] if launch == "1" else [5, 6, 7, 8])
    record = record_for("h100", "1")
    first, last = P.stage_script("h100", "1", 1, record), P.stage_script("h100", "1", None, record)
    assert "--stop-after-round 1" in first and "--stop-after-round" not in last
    for script in (first, last):
        assert script.startswith("#!/usr/bin/env bash") and '. rerun/h100-env.sh' in script and "exec python run_campaign.py" in script
    assert '. rerun/cpu-env.sh' in P.stage_script("c-gcp", "1", None, record_for("c-gcp", "1", credit_funded="no"))


# ---- the bring-up script -------------------------------------------------------------------------------------------------------

def bringup(host, launch="1", **extra):
    return P.bringup_script(host, launch, record_for(host, launch, **extra))


@pytest.mark.parametrize("host, flags", [
    ("h100", "s_setup() { ./setup_h100.sh --envs gpu; }"),
    ("a100", "s_setup() { ./setup_h100.sh --envs gpu --expect-gpu A100 --min-free-gib 450; }"),
    ("gh200", "s_setup() { ./setup_h100.sh --envs gpu; }"),
    ("c-gcp", "s_setup() { ./setup_h100.sh --cpu-only --enable-rapl-read --install-power-tools; }"),
    ("c-vm", "s_setup() { ./setup_h100.sh --cpu-only; }")])
def test_setup_flags_per_host_class(host, flags):
    script = bringup(host)
    assert flags in script
    if host in ("a100",):
        assert "--min-free-gib 250" in script and "--allow-any-gpu" in script           # Paper 1's A100 history (REPRODUCE.md, known caveats)
    if host == "gh200":
        assert "--allow-any-gpu" in script and not re.search(r"--expect-gpu(?!-)", script)   # setup_h100.sh itself expects GH200 on aarch64
    if host == "h100":
        assert "--allow-any-gpu" not in script


def test_inputs_are_generated_and_verified_with_the_paper_1_commands_and_c_vm_skips_sf100():
    h100, vm = bringup("h100"), bringup("c-vm")
    for line in ('python generate_data.py --root "$DATA_ROOT" --scales 1 10 30 --threads 30 --memory-limit 64GB',
                 'python generate_large_data.py --root "$DATA_ROOT" --scales 300 --workers 16',
                 'python verify_generated_inputs.py --data-root "$DATA_ROOT" --scales 1 10 30 --reference-dbgen --report rerun/verify-sf1-30-ref.json'):
        assert line in h100 and line in vm
    assert '--scales 100 --workers 12' in h100 and '--scales 100 --workers 12' not in vm
    assert '--scales 100 300 --report rerun/verify-sf100-300.json' in h100
    assert '--scales 300 --report rerun/verify-sf300.json' in vm and 'verify-sf100-300' not in vm


def test_preflight_lines_follow_the_pin_and_the_energy_source_of_the_host():
    assert '--jar "$RAPIDS_JAR" --skip-inputs --expect-gpu-unsupported cx_q13 cx_window' in bringup("h100")
    gcp = bringup("c-gcp", credit_funded="no")
    assert "python preflight.py --cpu-only --cpu-list 0-12,96-108 --cpu-energy require" in gcp
    vm = bringup("c-vm")
    assert "python preflight.py --cpu-only --data-root" in vm and "--cpu-list" not in vm and "--cpu-energy" not in vm


def test_every_bringup_script_is_valid_bash_and_ends_with_its_steps_in_order(monkeypatch):
    monkeypatch.setattr(P, "check_admitted", lambda *a, **k: None)                       # b200: admission is tested on its own
    for host, launch in LAUNCHES:
        extra = {"credit_funded": "no"} if host.startswith("c-") else {}
        script = bringup(host, launch, **extra)
        done = subprocess.run([BASH, "-n"], input=script, capture_output=True, text=True)
        assert done.returncode == 0, (host, done.stderr)
        steps = re.findall(r"^run_step (\w+) ", script, re.M)
        expected = ["boot_wait", "checkout", "lease", "setup", "hostinfo", "generate", "verify"] + (["pincheck", "preflight", "capture_before", "quiesce", "capture_after"] if host == "c-gcp" else ["preflight"]) + ["artifacts"]
        assert steps == expected, host
        assert f"git -C \"$STUDY\" checkout --detach {'a' * 40}" in script and "refs/tags/paper3-design-lock^{commit}" in script
        assert "campaign.json exists" in script and f"rerun/paper3-{host}-l{launch}" in script


def test_the_bringup_script_refuses_what_it_cannot_do_safely():
    with pytest.raises(P.PlanError, match="40-character"):
        P.bringup_script("h100", "1", {**record_for("h100", "1"), "campaign_commit": "main"})
    with pytest.raises(P.PlanError, match="lease"):
        P.bringup_script("h100", "1", {**record_for("h100", "1"), "lease_name": "my-instance"})


def test_c_gcp_masks_scheduled_timers_before_the_second_capture_and_cron_with_them():
    script = bringup("c-gcp", credit_funded="no")
    for unit in ("apt-daily.timer", "apt-daily-upgrade.timer", "fstrim.timer", "logrotate.timer", "cron.service"):
        assert unit in script
    assert script.index("capture exclusivity-before.json") < script.index("s_quiesce()") < script.index("capture exclusivity-after.json")
    assert "REVIEW-REQUIRED exclusivity-after.json" in script
    assert "REVIEW-REQUIRED" not in bringup("h100")


# ---- the allow-list change guard ----------------------------------------------------------------------------------------------

def make_repo(tmp_path, versions):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    commits = []
    for files in versions:
        for name, content in files.items():
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content if isinstance(content, str) else json.dumps(content, indent=1))
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "c", "--allow-empty"], cwd=repo, check=True, env=env)
        commits.append(subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip())
    return repo, commits


def hosts_file(allowed=None, **changes):
    document = json.loads((REPO / "paper3_hosts.json").read_text())
    document["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"] = allowed
    for dotted, value in changes.items():
        host, key = dotted.split("__")
        document["hosts"][host][key] = value
    return document


def test_filling_the_allow_list_is_the_only_change_the_guard_accepts(tmp_path, monkeypatch):
    items = ["cron:e2scrub_all", "timer:systemd-tmpfiles-clean.timer"]
    repo, (old, new, other, reset, nonstr, stale) = make_repo(tmp_path, [
        {"paper3_hosts.json": hosts_file()},
        {"paper3_hosts.json": hosts_file(items)},
        {"paper3_hosts.json": hosts_file(items, **{"h100__t_min_s": 2.0})},
        {"paper3_hosts.json": hosts_file(["timer:other.timer"])},
        {"paper3_hosts.json": hosts_file([1, 2])},
        {"paper3_hosts.json": hosts_file([1, 2]), "run_campaign.py": "# changed\n"}])
    monkeypatch.setattr(P, "REPO", repo)
    result = P.check_allowlist_change(old, new)
    assert result["allowed_scheduled"] == items and result["filled_now"] and result["changed_files"] == ["paper3_hosts.json"]
    with pytest.raises(P.PlanError, match="entry of h100 changed"):
        P.check_allowlist_change(new, other)                        # another host's value would change that host's design digest
    with pytest.raises(P.PlanError, match="already set and is now different"):
        P.check_allowlist_change(new, reset)                       # fixed for the whole campaign once set
    with pytest.raises(P.PlanError, match="list of strings"):
        P.check_allowlist_change(old, nonstr)
    with pytest.raises(P.PlanError, match="list of strings"):
        P.check_allowlist_change(old, old)                         # still null: nothing was reviewed
    with pytest.raises(P.PlanError, match="files other than"):
        P.check_allowlist_change(nonstr, stale)                    # code that runs during a measurement changed between the two commits


def test_the_guard_lets_the_runbook_and_its_tools_travel_with_the_allow_list_but_not_the_harness(tmp_path, monkeypatch):
    items = ["cron:e2scrub_all"]
    repo, (old, tools, harness) = make_repo(tmp_path, [
        {"paper3_hosts.json": hosts_file(), "tools/paper3_campaign/p3_plan.py": "# v1\n", "run_experiment.py": "# v1\n", "docs/PAPER3_RUNBOOK.md": "v1\n"},
        {"paper3_hosts.json": hosts_file(items), "tools/paper3_campaign/p3_plan.py": "# v2: one more masked unit\n", "docs/PAPER3_RUNBOOK.md": "v2\n"},
        {"paper3_hosts.json": hosts_file(items), "run_experiment.py": "# v2\n"}])
    monkeypatch.setattr(P, "REPO", repo)
    assert P.check_allowlist_change(old, tools)["changed_files"] == ["docs/PAPER3_RUNBOOK.md", "paper3_hosts.json", "tools/paper3_campaign/p3_plan.py"]
    with pytest.raises(P.PlanError, match="files other than"):
        P.check_allowlist_change(old, harness)


def test_the_guard_rejects_a_change_of_any_other_field_of_c_gcp(tmp_path, monkeypatch):
    repo, (old, new) = make_repo(tmp_path, [{"paper3_hosts.json": hosts_file()}, {"paper3_hosts.json": hosts_file([], **{"c-gcp__t_min_s": 3.0})}])
    monkeypatch.setattr(P, "REPO", repo)
    with pytest.raises(P.PlanError, match="field other than"):
        P.check_allowlist_change(old, new)
    repo2, (old2, new2) = make_repo(tmp_path / "second", [{"paper3_hosts.json": hosts_file()}, {"paper3_hosts.json": hosts_file([])}])
    monkeypatch.setattr(P, "REPO", repo2)
    assert P.check_allowlist_change(old2, new2)["allowed_scheduled"] == []                  # an empty list is a valid review result: nothing may be scheduled


# ---- the QA view: the blinding by construction -----------------------------------------------------------------------------------

CANARY = "987654.321"


def make_root(tmp_path):
    root = tmp_path / "paper3-h100-l1"
    (root / "round-1" / "baseline" / "sf30").mkdir(parents=True)
    (root / "round-1" / "baseline" / "results.json").write_text(json.dumps({"energy_j": CANARY, "wall_seconds": CANARY}))
    (root / "round-1" / "baseline" / "gpu.csv").write_text(f"t,power\n0,{CANARY}\n")
    (root / "round-1" / "baseline" / "schedule.json").write_text(json.dumps([{"engine": "polars", "seconds": CANARY}]))
    (root / "status.json").write_text(json.dumps({
        "state": "paused", "message": "paused after round 1", "updated_utc": "2026-11-17T12:00:00Z",
        "current": {"phase": "between_sessions", "round": 1, "suite": "baseline", "scale": "30", "engine": "polars", "session_started_utc": "x"},
        "sessions": {"completed": 32, "total": 128, "failed_among_run": 0, "run_including_repeats": 32},
        "rounds": {"main-r1": {"sessions": 32, "attempts": 1, "accepted_dir": "/x/round-1"}},
        "eta": {"remaining_seconds": 70000, "eta_utc": "2026-11-18T07:00:00Z", "basis": {"exact": 3}},
        "paper3": {"host": "h100", "launch": "1", "squares_complete": [], "confirmatory_rounds": [], "sensitivity_only_rounds": [1]}}))
    (root / "round-1" / "gates.json").write_text(json.dumps({
        "verdict": "pass", "reasons": [], "design_conformance": {"passed": True, "problems": []}, "preflight": {"verdict": "pass"},
        "run": {"baseline": {"started_utc": "2026-11-17T06:00:00Z", "finished_utc": "2026-11-17T09:00:00Z"}},
        "paper3": {"evaluated": True, "round_failed": False, "round_failures": ["G7 baseline/sf30/polars: gap 2.1 s"],
                   "gates": {"G6": {"pass": 32, "fail": 0, "not_evaluable": 0, "not_applicable": 0}, "G8": {"pass": 20, "fail": 0, "not_evaluable": 12, "not_applicable": 0}},
                   "ineligible_keys": 3}}))
    (root / "round-1" / "host-exclusivity-start.json").write_text(json.dumps({"label": "start", "scheduled": {"systemd_timers": ["a.timer"], "cron_files": []}}))
    return root


def test_the_qa_view_shows_progress_and_gate_outcomes_and_never_a_study_value(tmp_path):
    root = make_root(tmp_path)
    for detail in (False, True):
        text, as_json = Q.text(Q.view(root, detail)), json.dumps(Q.view(root, detail))
        assert CANARY not in text + as_json
        assert "state paused" in text and "sessions 32/128" in text and "G6 p32/f0/n0" in text and "G8 p20/f0/n12" in text
        assert "pooled ETA (all engines together): 2026-11-18T07:00:00Z" in text
        assert "started_utc" not in as_json and "finished_utc" not in as_json and "session_started" not in as_json         # no durations
    detailed = Q.view(root, True)
    assert detailed["attempts"][0]["paper3"]["failure_text"] == ["G7 baseline/sf30/polars: gap 2.1 s"]
    assert Q.view(root, False)["attempts"][0]["paper3"]["failing"] == ["G7 baseline/sf30/polars"]


@pytest.mark.parametrize("relative", ["round-1/baseline/results.json", "round-1/baseline/gpu.csv", "round-1/baseline/schedule.json",
                                      "round-1/baseline/sf30/power.csv", "round-1/baseline/host.csv", "campaign.json", "invocations.jsonl",
                                      "../status.json", "round-1/../round-2/gates.json", "/etc/passwd", "round-1/gates.json.bak", "round-x/gates.json"])
def test_the_qa_view_refuses_every_file_that_is_not_a_qa_file(tmp_path, relative):
    root = make_root(tmp_path)
    with pytest.raises(Q.ForbiddenRead):
        Q.read(root, relative)


def test_the_qa_view_opens_nothing_but_its_whitelist(tmp_path, monkeypatch):
    root = make_root(tmp_path)
    opened = []
    real = Path.read_text

    def spy(self, *a, **k):
        opened.append(self.relative_to(root).as_posix())
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", spy)
    Q.view(root, True)
    assert opened and all(Q.ALLOWED.match(name) for name in opened), opened


def test_the_qa_view_command_line(tmp_path):
    root = make_root(tmp_path)
    done = subprocess.run([sys.executable, str(TOOLS / "p3_qa_view.py"), str(root), "--json"], capture_output=True, text=True)
    assert done.returncode == 0 and json.loads(done.stdout)["state"] == "paused" and CANARY not in done.stdout


# ---- the host-side pack script -------------------------------------------------------------------------------------------------

pytestmark_shell = pytest.mark.skipif(BASH is None or shutil.which("shasum") is None, reason="bash and shasum are needed")


def pack(tmp_path, *args, study=None):
    home = tmp_path / "home"
    study = study or tmp_path / "study"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "HOME": str(home), "P3_STUDY_DIR": str(study)}
    return subprocess.run([BASH, str(TOOLS / "p3_host_pack.sh"), *args], capture_output=True, text=True, env=env, timeout=60), home


@pytestmark_shell
def test_pack_writes_an_archive_a_manifest_and_a_last_atomic_hash(tmp_path):
    study = tmp_path / "study"
    root = study / "rerun" / "paper3-x-l1"
    (root / "round-1" / "baseline").mkdir(parents=True)
    (root / "round-1-repeat-1").mkdir()
    (root / "round-2").mkdir()
    (root / "round-1" / "baseline" / "a.txt").write_text("a\n")
    (root / "round-1-repeat-1" / "b.txt").write_text("b\n")
    (root / "round-2" / "c.txt").write_text("c\n")
    (root / "campaign.json").write_text("{}\n")
    done, home = pack(tmp_path, "r1", "rerun/paper3-x-l1/round-1", "rerun/paper3-x-l1/round-1-repeat-*", "rerun/paper3-x-l1/campaign.json", "rerun/none-*")
    assert done.returncode == 0 and done.stdout.startswith("PACK-OK r1 files=3 "), done.stderr
    archive = home / "p3out" / "pack" / "r1.tar.gz"
    out = tmp_path / "out"
    out.mkdir()
    subprocess.run(["tar", "-xzf", str(archive), "-C", str(out)], check=True)
    names = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
    assert names == ["MANIFEST-r1.sha256", "rerun/paper3-x-l1/campaign.json", "rerun/paper3-x-l1/round-1-repeat-1/b.txt", "rerun/paper3-x-l1/round-1/baseline/a.txt"]
    assert "round-2" not in "".join(names)                                         # only what was asked for
    assert subprocess.run(["shasum", "-a", "256", "-c", "MANIFEST-r1.sha256"], cwd=out, capture_output=True).returncode == 0
    assert subprocess.run(["shasum", "-a", "256", "-c", "r1.tar.gz.sha256"], cwd=archive.parent, capture_output=True).returncode == 0
    assert not list((home / "p3out" / "pack").glob(".tmp.*")) and not list(archive.parent.glob("*.tmp"))


@pytestmark_shell
@pytest.mark.parametrize("args, code", [(("bad name", "x"), 2), (("n", "/etc/passwd"), 2), (("n", "../x"), 2), (("n",), 2), (("n", "rerun/nothing-*"), 3)])
def test_pack_refuses_unsafe_or_empty_requests(tmp_path, args, code):
    (tmp_path / "study").mkdir()
    done, _ = pack(tmp_path, *args)
    assert done.returncode == code


# ---- the generated blocks of the runbook ---------------------------------------------------------------------------------------

def test_the_runbook_exists_and_its_generated_blocks_are_current():
    runbook = REPO / "docs" / "PAPER3_RUNBOOK.md"
    assert runbook.exists()
    assert P.docs(runbook, write=False) == [], "run: python3 tools/paper3_campaign/p3_plan.py docs docs/PAPER3_RUNBOOK.md --write"


def test_docs_detects_a_stale_block_and_rewrites_it(tmp_path):
    path = tmp_path / "r.md"
    path.write_text("# x\n<!-- p3-gen: table -->\nold\n<!-- /p3-gen -->\ntext\n")
    assert P.docs(path, write=False) == ["table "]
    assert path.read_text().count("old") == 1
    assert P.docs(path, write=True) == ["table "]
    assert "old" not in path.read_text() and P.docs(path, write=False) == [] and path.read_text().endswith("text\n")
