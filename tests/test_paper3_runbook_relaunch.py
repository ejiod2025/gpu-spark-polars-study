"""Relaunching a launch once (amendment A1 item 2): the lease token, the record, the directory, the refusals, and everything that must keep working with the new record.

2026-11-23: the C-VM launch 1 (and then C-GCP launch 1) stopped by the campaign's own rule on a preflight defect that was in the tool, not in the data; the lead's decision is to
launch each once more under A1 item 2, never pooling the stopped rounds with the replacement. The replacement has its own lease (p3-campaign-cvm-l1r2-...), its own record
(ordinal 2, relaunch_of the old lease), its own local directory (rerun/paper3/c-vm/launch1-r2), and the same campaign root and launch number on its new host, so that the analysis
finds it by its campaign.json and labels it c-vm-l1r2 (paper3_analysis/tree.py). Nothing copied from the first launch may be touched.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "tools" / "paper3_campaign"
for path in (str(TOOLS), str(REPO), str(REPO / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import analyze_paper3 as A  # noqa: E402
import p3_plan as P  # noqa: E402
import paper3_analysis.tree as tree_mod  # noqa: E402
import probe_power  # noqa: E402
from paper3_tree_fakes import TreeBuilder, World  # noqa: E402
from test_paper3_controller import ctl  # noqa: E402,F401
from test_paper3_runbook_ops import COMMIT, clock, drive, launch, sh, snapshot, wait_for, world, write_record  # noqa: E402,F401

NOW = P.utc("2026-11-23T06:00:00Z")
HOSTS = [("a100", "1"), ("h100", "2"), ("c-gcp", "1"), ("c-vm", "1")]


def planner(*argv):
    return subprocess.run([sys.executable, str(TOOLS / "p3_plan.py"), *map(str, argv)], capture_output=True, text=True)


# ---- the planner: names, labels, directories --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("host, launch_no", HOSTS)
def test_a_relaunch_lease_carries_the_token_and_still_satisfies_the_lease_convention(host, launch_no):
    first, second = P.lease_spec(host, launch_no, NOW), P.lease_spec(host, launch_no, NOW, ordinal=2)
    assert f"-l{launch_no}-owner-" in first["lease_name"] and "r2" not in first["lease_name"] and first["ordinal"] == 1, "a first launch is named exactly as before"
    assert f"-l{launch_no}r2-owner-ejiod-expires-" in second["lease_name"] and second["lease_name"] != first["lease_name"] and second["ordinal"] == 2
    for spec in (first, second):
        status = probe_power.lease_status(spec["lease_name"], now=NOW)
        assert status["valid"] and status["owner"] == "ejiod" and status["purpose"].startswith("campaign-")
    if P.plan_of(host).provider == "gcp":
        assert len(second["lease_name"]) <= 63 and second["lease_name"] == second["lease_name"].lower()


@pytest.mark.parametrize("bad", [0, 3, -1])
def test_only_the_first_instance_and_one_relaunch_exist(bad):
    with pytest.raises(P.PlanError, match="relaunched at most once"):
        P.lease_spec("c-vm", "1", NOW, ordinal=bad)
    assert planner("lease", "c-vm", "1", "--ordinal", bad).returncode == 2


def test_the_gcp_request_of_a_relaunch_labels_the_instance_with_the_token_and_changes_nothing_else():
    first = P.launch_spec("c-vm", "1", "us-east4-a", NOW)
    second = P.launch_spec("c-vm", "1", "us-east4-a", NOW, ordinal=2)
    assert first["labels"]["launch"] == "l1" and second["labels"]["launch"] == "l1r2"
    strip = lambda command, lease: [word.replace(lease, "<LEASE>") for word in command]  # noqa: E731
    for word in second["command"]:
        assert "r2" not in word or second["lease_name"] in word or word.startswith("--labels="), word
    differences = [(a, b) for a, b in zip(strip(first["command"], first["lease_name"]), strip(second["command"], second["lease_name"])) if a != b]
    assert differences == [(first["labels"] and next(w for w in first["command"] if w.startswith("--labels=")).replace(first["lease_name"], "<LEASE>"),
                            next(w for w in second["command"] if w.startswith("--labels=")).replace(second["lease_name"], "<LEASE>"))]
    assert first["boot_disk"] == second["boot_disk"] and first["access"] == second["access"] and first["termination_action"] == second["termination_action"]


def test_a_lambda_relaunch_names_the_instance_with_the_token_too():
    spec = P.launch_spec("a100", "1", "us-east-1", NOW, ordinal=2)
    assert spec["body"]["name"] == spec["lease_name"] and "-l1r2-owner-" in spec["body"]["name"]


def test_the_directory_of_a_relaunch_is_its_own_and_the_shell_and_the_planner_agree(world):
    assert P.launch_dir_name("1") == "launch1" and P.launch_dir_name("1", 2) == "launch1-r2" and P.launch_dir_name("2", 2) == "launch2-r2"
    assert planner("dest", "c-vm", "1", "--ordinal", "2").stdout.strip() == "rerun/paper3/c-vm/launch1-r2"
    assert planner("dest", "c-vm", "1").stdout.strip() == "rerun/paper3/c-vm/launch1"
    for extra, want in (({}, "launch1"), ({"ordinal": 1}, "launch1"), ({"ordinal": None}, "launch1"), ({"ordinal": 2}, "launch1-r2")):
        record = write_record(world, host="c-vm", launch="1", **extra)
        done = subprocess.run(["bash", "-c", f'. "{TOOLS}/p3_ops_lib.sh"; p3_launch_dir "{record}"'], capture_output=True, text=True, env=world.env)
        assert done.stdout.strip() == want, (extra, done.stderr)
        record.unlink()


# ---- check-relaunch: the refusals -------------------------------------------------------------------------------------------------------------

def copied_launch(directory: Path, files=("rerun/paper3-c-vm-l1/round-1/gates.json", "rerun/paper3-c-vm-l1/status.json", "launch-record.json")):
    """A copied launch directory the way the driver leaves it: files and a LAUNCH-SHA256SUMS over them."""
    lines = []
    for name in files:
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"content of {name}\n")
        lines.append(f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {name}")
    (directory / "LAUNCH-SHA256SUMS").write_text("\n".join(sorted(lines)) + "\n")
    return directory


@pytest.fixture
def stopped(world):
    """The record of a C-VM launch 1 that stopped by rule: instance confirmed terminated, its directory copied and sealed."""
    record = write_record(world, host="c-vm", launch="1", provider="gcp", instance_id="7777", zone="us-east4-a", region=None, instance_type="c3-highmem-44",
                          terminate_confirmed_utc="2026-11-23T21:50:00Z")
    old = copied_launch(world.tmp / "old-launch")
    return type("Stopped", (), {"record": record, "dir": old, "lease": json.loads(record.read_text())["lease_name"], "world": world})


def check(stopped, **kw):
    args = ["check-relaunch", "c-vm", "1", "--old", kw.get("old", stopped.record), "--ledger", stopped.world.ledger, "--old-dest", kw.get("dest", stopped.dir)]
    return planner(*args)


def test_a_terminated_and_copied_launch_may_be_relaunched_once(stopped):
    done = check(stopped)
    assert done.returncode == 0, done.stderr
    answer = json.loads(done.stdout)
    assert answer["ordinal"] == 2 and answer["relaunch_of"] == stopped.lease and answer["old_files_verified"] == 3
    assert answer["new_dest"].endswith("rerun/paper3/c-vm/launch1-r2") and answer["root"] == "rerun/paper3-c-vm-l1", "the same campaign root and launch number on the new host"
    assert check(stopped, old=stopped.lease).returncode == 0, "the old launch may be named by its lease"


def test_a_launch_whose_instance_is_not_confirmed_terminated_is_not_relaunched(stopped):
    record = json.loads(stopped.record.read_text())
    record["terminate_confirmed_utc"] = None
    stopped.record.write_text(json.dumps(record))
    done = check(stopped)
    assert done.returncode == 2 and "terminate_confirmed_utc" in done.stderr


def test_a_launch_whose_copy_is_missing_or_damaged_is_not_relaunched(stopped):
    (stopped.dir / "LAUNCH-SHA256SUMS").rename(stopped.dir / "SUMS-hidden")
    missing = check(stopped)
    assert missing.returncode == 2 and "no LAUNCH-SHA256SUMS" in missing.stderr
    (stopped.dir / "SUMS-hidden").rename(stopped.dir / "LAUNCH-SHA256SUMS")
    assert check(stopped).returncode == 0
    (stopped.dir / "rerun" / "paper3-c-vm-l1" / "status.json").write_text("changed after the copy\n")
    damaged = check(stopped)
    assert damaged.returncode == 2 and "status.json (differs)" in damaged.stderr
    (stopped.dir / "rerun" / "paper3-c-vm-l1" / "status.json").unlink()
    gone = check(stopped)
    assert gone.returncode == 2 and "status.json (missing)" in gone.stderr
    (stopped.dir / "LAUNCH-SHA256SUMS").write_text("")
    assert check(stopped).returncode == 2


def test_the_copy_is_found_in_the_drivers_directory_when_no_old_dest_is_given(stopped, tmp_path, monkeypatch):
    """The default is rerun/paper3/HOST/launchN of the repository; the repository is a temporary one here (the real rerun/paper3 holds study data and is never read by a test)."""
    monkeypatch.setattr(P, "REPO", tmp_path / "repo")
    missing = pytest.raises(P.PlanError, match=str(tmp_path / "repo" / "rerun" / "paper3" / "c-vm" / "launch1"))
    with missing:
        P.check_relaunch("c-vm", "1", str(stopped.record), stopped.world.ledger)
    copied_launch(tmp_path / "repo" / "rerun" / "paper3" / "c-vm" / "launch1")
    answer = P.check_relaunch("c-vm", "1", str(stopped.record), stopped.world.ledger)
    assert answer["old_dest"].endswith("rerun/paper3/c-vm/launch1") and answer["new_dest"] == str(tmp_path / "repo" / "rerun" / "paper3" / "c-vm" / "launch1-r2")
    assert answer["old_files_verified"] == 3


def test_a_launch_is_relaunched_once_whether_the_second_try_names_the_first_or_the_relaunch(stopped):
    relaunch = write_record(world=stopped.world, host="c-vm", launch="1", provider="gcp", instance_id="8888", zone="us-east4-c", region=None, instance_type="c3-highmem-44",
                            ordinal=2, relaunch_of=stopped.lease, lease_name=P.lease_spec("c-vm", "1", NOW, ordinal=2)["lease_name"], terminate_confirmed_utc="2026-11-24T04:00:00Z")
    again = check(stopped)
    assert again.returncode == 2 and "already a relaunch" in again.stderr and json.loads(relaunch.read_text())["lease_name"] in again.stderr
    copied_launch(stopped.world.tmp / "relaunch-dir", files=("rerun/paper3-c-vm-l1/status.json", "launch-record.json"))
    of_the_relaunch = check(stopped, old=relaunch, dest=stopped.world.tmp / "relaunch-dir")
    assert of_the_relaunch.returncode == 2 and "already the relaunch of" in of_the_relaunch.stderr
    relaunch.unlink()
    assert check(stopped).returncode == 0, "no relaunch record: allowed"


def test_a_record_of_another_host_or_launch_is_not_the_old_launch(stopped):
    other = write_record(stopped.world, host="c-gcp", launch="1", provider="gcp", terminate_confirmed_utc="2026-11-23T21:50:00Z")
    done = check(stopped, old=other)
    assert done.returncode == 2 and "not of c-vm launch 1" in done.stderr
    assert check(stopped, old="p3-campaign-cvm-l1-owner-ejiod-expires-20990101t0000z").returncode == 2


# ---- the short allow-list review -----------------------------------------------------------------------------------------------------------

def test_the_short_review_lists_what_the_new_capture_holds_that_the_committed_list_lacks(tmp_path, monkeypatch):
    committed = {"hosts": {"c-gcp": {"exclusive_use": {"allowed_scheduled": ["timer:fstrim.timer", "cron:dpkg"]}}}}
    monkeypatch.setattr(P, "git_show", lambda commit, path: json.dumps(committed))
    capture = tmp_path / "exclusivity-after.json"
    capture.write_text(json.dumps({"allow_list_items": ["cron:dpkg", "timer:fstrim.timer", "timer:new-thing.timer"]}))
    review = P.allowlist_review(capture, "a" * 40)
    assert review["not_in_list"] == ["timer:new-thing.timer"] and review["capture_items"] == 3 and review["allow_list_items"] == 2
    capture.write_text(json.dumps({"allow_list_items": ["cron:dpkg"]}))
    assert P.allowlist_review(capture, "a" * 40)["not_in_list"] == []
    committed["hosts"]["c-gcp"]["exclusive_use"]["allowed_scheduled"] = None
    with pytest.raises(P.PlanError, match="first review"):
        P.allowlist_review(capture, "a" * 40)
    capture.write_text("{}")
    with pytest.raises(P.PlanError, match="cannot read allow_list_items"):
        P.allowlist_review(capture, "a" * 40)


# ---- the launcher ---------------------------------------------------------------------------------------------------------------------------

def relaunch_args(stopped, *more, snap=True, yes=True):
    args = ["c-vm", "1", "--place", "us-east4-c", "--commit", COMMIT, "--relaunch-of", stopped.record, "--old-dest", stopped.dir, "--credit-funded", "no", *more]
    if snap:
        args += ["--price-snapshot", snapshot(stopped.world)]
    return args + (["--yes"] if yes else [])


def test_the_dry_run_of_a_relaunch_says_what_it_is_and_calls_nothing(stopped):
    world = stopped.world
    done = launch(world, *relaunch_args(stopped, "--now", "2026-11-23T22:00:00Z", snap=False, yes=False))
    assert done.returncode == 0, done.stderr
    assert f"relaunch 2 of {stopped.lease}" in done.stdout and "its copy verifies (3 files" in done.stdout and "rerun/paper3/c-vm/launch1-r2" in done.stdout
    assert "p3-campaign-cvm-l1r2-owner-ejiod-expires-20261124t0800z" in done.stdout and "labels=project=paper3-energy" in done.stdout and "launch=l1r2" in done.stdout
    assert "DRY RUN" in done.stdout and not (world.net / "gcloud.log").exists()


def test_the_refusals_also_show_in_the_dry_run_and_launch_nothing(stopped):
    world = stopped.world
    (stopped.dir / "LAUNCH-SHA256SUMS").unlink()
    done = launch(world, *relaunch_args(stopped, snap=False, yes=False))
    assert done.returncode == 2 and "no LAUNCH-SHA256SUMS" in done.stderr and "DRY RUN" not in done.stdout
    assert launch(world, "c-vm", "1", "--place", "us-east4-c", "--commit", COMMIT, "--old-dest", stopped.dir).returncode == 2, "--old-dest belongs to --relaunch-of"
    assert not (world.net / "gcloud.log").exists() and len(list(world.ledger.glob("*.json"))) == 1


def test_a_relaunch_is_recorded_with_its_own_lease_ordinal_and_the_old_lease_and_arms_its_own_watchdog(stopped):
    world = stopped.world
    watchdog = world.tmp / "gwd.sh"
    watchdog.write_text('#!/bin/bash\necho "$@" > "$FAKE_NET/watchdog.args"\nexec sleep 30\n')
    watchdog.chmod(0o755)
    done = launch(world, *relaunch_args(stopped), P3_GCP_WATCHDOG=watchdog)
    assert done.returncode == 0, done.stdout + done.stderr
    records = [json.loads(p.read_text()) for p in world.ledger.glob("p3-*.json")]
    new = next(r for r in records if r["lease_name"] != stopped.lease)
    assert new["ordinal"] == 2 and new["relaunch_of"] == stopped.lease and new["host"] == "c-vm" and new["launch"] == 1 and "-l1r2-" in new["lease_name"]
    assert new["campaign_commit"] == COMMIT and new["terminate_confirmed_utc"] is None and new["zone"] == "us-east4-c"
    old = json.loads(stopped.record.read_text())
    assert old["lease_name"] == stopped.lease and old["terminate_confirmed_utc"] == "2026-11-23T21:50:00Z" and "relaunch_of" not in old, "the old record is not touched"
    create = (world.net / "gcloud.log").read_text().splitlines()[0]
    assert create.startswith(f"compute instances create {new['lease_name']} --zone=us-east4-c --machine-type=c3-highmem-44") and "launch=l1r2" in create
    assert wait_for(world.net / "watchdog.args").read_text().split() == [new["lease_name"], "us-east4-c", str(int(new["lease_hours"] * 3600))]
    ledger = [json.loads(line) for line in (world.ledger / "ledger.jsonl").read_text().splitlines()]
    entry = next(e for e in ledger if e["event"] == "launch" and e["lease_name"] == new["lease_name"])
    assert entry["ordinal"] == 2 and entry["relaunch_of"] == stopped.lease and entry["reason"] == f"relaunch 2 of {stopped.lease}"
    assert (world.ledger / f"watchdog-{new['lease_name']}.log").exists()
    subprocess.run(["kill", str(new["watchdog_pid"])], capture_output=True)


def test_a_second_relaunch_is_refused_after_a_first_one_exists(stopped):
    world = stopped.world
    watchdog = world.tmp / "gwd.sh"
    watchdog.write_text("#!/bin/bash\nexec sleep 30\n")
    watchdog.chmod(0o755)
    first = launch(world, *relaunch_args(stopped), P3_GCP_WATCHDOG=watchdog)
    assert first.returncode == 0, first.stderr
    creates = (world.net / "gcloud.log").read_text().count("compute instances create")
    second = launch(world, *relaunch_args(stopped, "--now", "2026-11-24T08:00:00Z"), P3_GCP_WATCHDOG=watchdog)
    assert second.returncode == 2 and "already a relaunch" in second.stderr
    assert (world.net / "gcloud.log").read_text().count("compute instances create") == creates and len(list(world.ledger.glob("p3-*.json"))) == 2
    for pid in [json.loads(p.read_text()).get("watchdog_pid") for p in world.ledger.glob("p3-*.json")]:
        if pid:
            subprocess.run(["kill", str(pid)], capture_output=True)


def test_a_plain_launch_of_a_launch_that_has_a_record_says_so_and_points_at_the_relaunch_option(stopped):
    done = launch(stopped.world, "c-vm", "1", "--place", "us-east4-c", "--commit", COMMIT, "--now", "2026-11-23T22:00:00Z")
    assert done.returncode == 0 and "NOTE: the ledger already holds a record of c-vm launch 1" in done.stdout and "--relaunch-of" in done.stdout and stopped.lease in done.stdout
    fresh = launch(stopped.world, "c-gcp", "1", "--place", "us-east4-c", "--commit", COMMIT, "--now", "2026-11-23T22:00:00Z")
    assert "NOTE:" not in fresh.stdout


# ---- the driver and the tools around it ---------------------------------------------------------------------------------------------------

def relaunch_record(world, host="c-vm", launch_no="1", **extra):
    provider = "gcp" if host in ("c-vm", "c-gcp") else "lambda"
    zone = "us-east4-c" if provider == "gcp" else None
    return write_record(world, host=host, launch=launch_no, provider=provider, zone=zone, region=None if provider == "gcp" else "us-south-2", ordinal=2,
                        relaunch_of="p3-campaign-cvm-l1-owner-ejiod-expires-20261123t0000z",
                        lease_name=P.lease_spec(host, launch_no, datetime.now(timezone.utc), ordinal=2)["lease_name"], **extra)


def test_a_relaunch_runs_to_a_teardown_in_its_own_directory_and_its_launch_record_says_what_it_is(drive):
    world = drive.world
    record = relaunch_record(world, host="h100", launch_no="1")
    name = json.loads(record.read_text())["lease_name"]
    done = drive(record, FAKE_ROUNDS="1")
    assert done.returncode == 0, done.log[-1500:] + done.stderr[-800:]
    kept = json.loads((done.dest / "launch-record.json").read_text())
    assert kept["lease_name"] == name and kept["ordinal"] == 2 and kept["relaunch_of"].startswith("p3-campaign-cvm-l1-") and "ip" not in kept
    assert (done.dest / ".lease").read_text().strip() == name and (done.dest / "LAUNCH-SHA256SUMS").exists()
    confirmed = [json.loads(line) for line in (world.ledger / "ledger.jsonl").read_text().splitlines() if "terminate-confirmed" in line]
    assert confirmed and confirmed[-1]["ordinal"] == 2 and confirmed[-1]["lease_name"] == name


def test_a_directory_that_holds_another_leases_copy_is_never_written_into(drive):
    world = drive.world
    first = write_record(world, host="h100", launch="1")
    done = drive(first, FAKE_ROUNDS="1")
    assert done.returncode == 0, done.log[-1200:]
    before = sorted(p.relative_to(done.dest).as_posix() for p in done.dest.rglob("*") if p.is_file() and "driver-" not in p.name)
    second = relaunch_record(world, host="h100", launch_no="1")
    refused = drive(second, FAKE_ROUNDS="1")                 # same --dest: the first launch's copied directory
    assert refused.returncode == 2 and "already holds the copied launch of" in refused.stderr and "not of" in refused.stderr
    marker = world.tmp / "dest" / ".lease"
    assert marker.read_text().strip() == json.loads(first.read_text())["lease_name"]
    (world.tmp / "dest" / "launch-record.json").unlink()
    again = drive(second, FAKE_ROUNDS="1")
    assert again.returncode == 2 and "belongs to lease" in again.stderr, "the .lease marker alone is enough"
    after = sorted(p.relative_to(done.dest).as_posix() for p in done.dest.rglob("*") if p.is_file() and "driver-" not in p.name and p.name != "launch-record.json")
    assert [n for n in before if n != "launch-record.json"] == after, "nothing in the first launch's directory changed"


def test_the_allow_list_review_applies_to_a_c_gcp_relaunch_and_shows_the_short_comparison(drive):
    world = drive.world
    record = relaunch_record(world, host="c-gcp", launch_no="1")
    name = json.loads(record.read_text())["lease_name"]
    env = {"FAKE_INSTANCE_NAME": name}
    first = drive(record, FAKE_NEW_ITEMS=json.dumps(["timer:new-thing.timer"]), **env)
    assert first.returncode == 10 and "REVIEW REQUIRED (the allow-list is committed" in first.log and "not in the committed list: timer:new-thing.timer" in first.log
    assert f"--config-commit {COMMIT}" in first.log and "mask it on the host before round 1 and never add it to the list" in first.log
    assert not list((world.host / "p3out").glob("campaign-*")), "no round starts before a person has looked"
    quiet = drive(record, FAKE_NEW_ITEMS="[]", **env)
    assert quiet.returncode == 10 and "not in the committed list: none" in quiet.log
    done = drive(record, "--config-commit", COMMIT, FAKE_ROUNDS="1", **env)
    assert done.returncode == 0, done.log[-1500:]
    assert (done.dest / ".verified" / "config").exists() and json.loads(record.read_text())["config_commit"] == COMMIT


def test_a_c_gcp_launch_whose_list_is_not_committed_yet_still_gets_the_first_review_message(drive):
    world = drive.world
    record = write_record(world, host="c-gcp", provider="gcp", instance_id="7777", zone="us-east4-c", region=None)
    name = json.loads(record.read_text())["lease_name"]
    done = drive(record, FAKE_NO_LIST="1", FAKE_INSTANCE_NAME=name)
    assert done.returncode == 10 and "decide the allow-list, commit it to paper3_hosts.json" in done.log and "short review" not in done.log


def test_status_teardown_and_the_gcp_watchdog_work_with_a_relaunch_record(world, clock):
    record = relaunch_record(world, host="c-vm", launch_no="1")
    data = json.loads(record.read_text())
    status = subprocess.run(["bash", "-c", f'. "{TOOLS}/p3_ops_lib.sh"; echo "$(record_get "{record}" ordinal) $(p3_launch_dir "{record}")"'], capture_output=True, text=True, env=world.env)
    assert status.stdout.split() == ["2", "launch1-r2"]
    # the campaign watchdog acts on the name with the new token, through the campaign label, and only that
    deleted = sh(world, TOOLS / "p3_gcp_watchdog.sh", data["lease_name"], "us-east4-c", 10, FAKE_INSTANCE_NAME=data["lease_name"])
    assert deleted.returncode == 0 and "deleted" in deleted.stdout
    (world.net / "instance").write_text("up")
    done = sh(world, TOOLS / "p3_teardown.sh", record, "--yes", FAKE_INSTANCE_NAME=data["lease_name"])
    assert done.returncode == 0, done.stderr
    assert json.loads(record.read_text())["terminate_confirmed_utc"] is not None
    ledger = [json.loads(line) for line in (world.ledger / "ledger.jsonl").read_text().splitlines()]
    assert {e["event"] for e in ledger if e["lease_name"] == data["lease_name"]} >= {"terminate-requested", "terminate-confirmed"}
    assert all(e["ordinal"] == 2 and e["relaunch_of"] == data["relaunch_of"] for e in ledger if e["lease_name"] == data["lease_name"] and e["event"].startswith("terminate"))


def test_the_status_line_of_a_relaunch_says_which_launch_it_replaces(drive):
    world = drive.world
    record = relaunch_record(world, host="h100", launch_no="1")
    assert drive(record, "--no-teardown", FAKE_ROUNDS="1").returncode == 0
    qa_dir = world.host / "study" / "tools" / "paper3_campaign"
    qa_dir.mkdir(parents=True)
    shutil.copy(TOOLS / "p3_qa_view.py", qa_dir / "p3_qa_view.py")
    status = sh(world, TOOLS / "p3_status.sh", record)
    old = json.loads(record.read_text())["relaunch_of"]
    assert status.returncode == 0, status.stderr
    assert status.stdout.splitlines()[0].startswith(f"lease {json.loads(record.read_text())['lease_name']} (relaunch 2 of {old}), expires ")
    plain = write_record(world, host="h100", launch="1", lease_name="p3-campaign-h100-l1-owner-ejiod-expires-20990101T0000Z")
    first = sh(world, TOOLS / "p3_status.sh", plain)
    assert first.stdout.splitlines()[0].startswith("lease p3-campaign-h100-l1-owner-ejiod-expires-20990101T0000Z, expires ")


def test_pull_takes_the_directory_of_a_relaunch_and_checks_it_against_its_own_manifest(ctl):
    base = ctl.tmp / "tarbase"
    launch_dir = base / "paper3" / "c-vm" / "launch1-r2"
    (launch_dir / "rerun" / "paper3-c-vm-l1").mkdir(parents=True)
    (launch_dir / "rerun" / "paper3-c-vm-l1" / "status.json").write_text('{"state": "complete"}\n')
    (launch_dir / "launch-record.json").write_text("{}\n")
    subprocess.run("shasum -a 256 rerun/paper3-c-vm-l1/status.json launch-record.json > LAUNCH-SHA256SUMS", shell=True, cwd=launch_dir, check=True)
    subprocess.run(["tar", "-cf", str(ctl.net / "launch.tar"), "-C", str(base), "paper3"], check=True)
    dest = ctl.tmp / "dest"
    done = ctl.run("pull", "c-vm", "1-r2", dest)
    assert done.returncode == 0, done.stderr
    assert "verified: every file of c-vm launch 1-r2 matches LAUNCH-SHA256SUMS" in done.stdout and (dest / "rerun" / "paper3" / "c-vm" / "launch1-r2" / "launch-record.json").exists()
    assert "paper3/c-vm/launch1-r2" in (ctl.net / "ctl.commands").read_text()
    for bad in ("1-r3", "3-r2", "1-r"):
        assert ctl.run("pull", "c-vm", bad).returncode == 2, bad


def test_the_archive_and_the_pull_name_the_directory_of_a_relaunch(world, tmp_path):
    base = tmp_path / "base"
    launch_dir = copied_launch(base / "rerun" / "paper3" / "c-vm" / "launch1-r2")
    done = sh(world, TOOLS / "p3_archive.sh", "c-vm", "1-r2", "--dir", base)
    assert done.returncode == 0, done.stderr
    assert "paper3-campaign/c-vm/launch1-r2" in done.stdout and str(launch_dir) in done.stdout and "DRY RUN" in done.stdout
    for bad in ("1-r3", "3-r2", "1-r", "r2", "1r2", "1-r2;ls"):
        assert sh(world, TOOLS / "p3_archive.sh", "c-vm", bad, "--dir", base).returncode == 2, bad
    first = sh(world, TOOLS / "p3_archive.sh", "c-vm", "1", "--dir", base)
    assert first.returncode == 2 and "no launch directory" in first.stderr, "the first launch's name never finds the relaunch's directory"


def test_the_open_log_tool_reads_the_held_logs_of_the_relaunch_directory_by_default(world, tmp_path):
    record = relaunch_record(world, host="c-vm", launch_no="1")
    text = (TOOLS / "p3_open_log.sh").read_text()
    assert 'p3_launch_dir "$RECORD"' in text and "launch$LAUNCH}" not in text
    refused = sh(world, TOOLS / "p3_open_log.sh", record, "bringup-a1.log", "--dest", tmp_path / "relaunch-dir")      # a temporary directory: the real rerun/paper3 is never touched by a test
    assert refused.returncode == 2 and "is not the log of a stage that failed" in refused.stderr
    (tmp_path / "relaunch-dir").mkdir()
    (tmp_path / "relaunch-dir" / ".held-logs").write_text("bringup-a1.log\n")
    (tmp_path / "relaunch-dir" / "bringup-a1.log").write_text("a synthetic log line\n")
    opened = sh(world, TOOLS / "p3_open_log.sh", record, "bringup-a1.log", "--dest", tmp_path / "relaunch-dir")
    assert opened.returncode == 0 and "a synthetic log line" in opened.stdout and "OPENED bringup-a1.log" in (tmp_path / "relaunch-dir" / "log-openings.log").read_text()
    ledger = [json.loads(line) for line in (world.ledger / "ledger.jsonl").read_text().splitlines()]
    assert ledger[-1]["event"] == "held-log-opened" and ledger[-1]["lease_name"] == json.loads(record.read_text())["lease_name"]


# ---- the analysis tree ------------------------------------------------------------------------------------------------------------------

def test_a_cvm_relaunch_in_its_own_directory_is_labelled_l1r2_and_never_pooled_with_the_stopped_launch(tmp_path):
    world = World()
    builder = TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    stopped = [{"round": 1, "index": 0, "verdict": "fail"}, {"round": 1, "index": 1, "verdict": "fail"}]
    builder.add_launch("c-vm", 1, [1], attempts=stopped, created="2026-11-23T16:00:00+00:00", launch_name="launch1", state="stopped")
    builder.add_launch("c-vm", 1, [1, 2], created="2026-11-24T01:00:00+00:00", launch_name="launch1-r2")
    assert (tmp_path / "tree" / "c-vm" / "launch1").is_dir() and (tmp_path / "tree" / "c-vm" / "launch1-r2").is_dir()
    tree = tree_mod.load_tree(tmp_path / "tree")
    host = tree.hosts["c-vm"]
    assert [i.label for i in host.instances] == ["c-vm-l1", "c-vm-l1r2"] and [i.ordinal for i in host.instances] == [1, 2]
    assert {i.label: i.root.parent.parent.name for i in host.instances} == {"c-vm-l1": "launch1", "c-vm-l1r2": "launch1-r2"}, "each instance is its own directory"
    assert [i.label for i in host.complete.values()] == ["c-vm-l1r2"]
    confirmatory_instances = {instance.label for instance, _ in host.confirmatory.values()}
    assert confirmatory_instances == {"c-vm-l1r2"} and sorted(host.confirmatory) == [1, 2]
    assert host.instances[0].accepted == {}, "the stopped launch has no accepted round: its failed attempts are never pooled with anything"


def test_the_stopped_launchs_rounds_that_were_accepted_stay_a_sensitivity_and_are_not_pooled(tmp_path):
    world = World()
    builder = TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    first = [{"round": 1, "index": 0, "verdict": "pass"}, {"round": 2, "index": 0, "verdict": "fail"}, {"round": 2, "index": 1, "verdict": "fail"}]
    builder.add_launch("c-vm", 1, [1], attempts=first, created="2026-11-23T16:00:00+00:00", launch_name="launch1", state="stopped")
    builder.add_launch("c-vm", 1, [1, 2], created="2026-11-24T01:00:00+00:00", launch_name="launch1-r2")
    host = tree_mod.load_tree(tmp_path / "tree").hosts["c-vm"]
    assert {i.label for i, _ in host.confirmatory.values()} == {"c-vm-l1r2"} and sorted(host.confirmatory) == [1, 2]
    assert sorted(host.sensitivity) == ["c-vm-l1"] and sorted(host.sensitivity["c-vm-l1"]) == [1], "round 1 of the stopped launch is reported on its own, never averaged with the relaunch's"


def test_the_whole_analysis_labels_the_relaunch_and_takes_its_rounds_from_the_relaunch_alone(tmp_path):
    world = World()
    builder = TreeBuilder(tmp_path / "tree", world.truth(), grid={"baseline": ("30",), "complex": ("30",)})
    stopped = [{"round": 1, "index": 0, "verdict": "fail"}, {"round": 1, "index": 1, "verdict": "fail"}]
    builder.add_launch("c-vm", 1, [1], attempts=stopped, created="2026-11-23T16:00:00+00:00", launch_name="launch1", state="stopped")
    builder.add_launch("c-vm", 1, [1, 2], created="2026-11-24T01:00:00+00:00", launch_name="launch1-r2")
    builder.add_launch("c-gcp", 1, [1, 2, 3, 4])
    host = A.run_analysis(tmp_path / "tree", tmp_path / "out", unblind=True, allow_synthetic=True)["hosts"]["c-vm"]
    assert [i["label"] for i in host["instances"]] == ["c-vm-l1", "c-vm-l1r2"]
    assert host["squares"]["1"] == {"complete": True, "instance": "c-vm-l1r2", "rounds": [1, 2]}
    assert host["confirmatory_rounds"] == [1, 2] and host["sensitivity_rounds"] == []
