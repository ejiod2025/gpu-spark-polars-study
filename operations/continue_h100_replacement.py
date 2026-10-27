#!/usr/bin/env python3
"""Continue the authorized replacement preparation into its fixed campaign.

Run locally, detached, using the analysis interpreter. This controller waits
for preparation, preserves a new prestart archive in the private branch before
acknowledging it, then runs the existing block-backup watcher. It never retries
an engine session or restarts a supervisor. Existing control state requires an
explicit --resume-controller after investigation. Use the analysis interpreter
and keep stdout/stderr in an ignored persistent local log, for example:

    python -B operations/continue_h100_replacement.py \
      --host ubuntu@68.209.73.247 --identity-file /Users/danielejiofor/.ssh/lambda_key22 \
      --repository /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study \
      --evidence /home/ubuntu/study-h100-research-evidence-20261027 \
      --destination /Users/danielejiofor/AIRFLOW/gpu-spark-polars-study/rerun/replacement-20261027/raw-backups
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

SOURCE_COMMIT = "c84ef6a07341f350dae6636ab709c1b65fd894e0"
SOURCE_TREE = "9514f0f7ce384763d0a675a9075666707e0edabcc003c729a3e0735a52ea25d8"
CAMPAIGN_ID = "h100-confirmatory-replacement-20261027"
BRANCH = "H100_main_dev"
REPOSITORY = "ejiod2025/gpu-spark-polars-study"
REGISTRY = "research_reporting/campaign_identities.json"
TOOLS = ("operations/research_backup.py", "operations/run_h100_replacement.sh",
         "operations/record_replacement_inputs.py", "operations/continue_h100_replacement.py")


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def write_state(path, value):
    value["updated_at_utc"] = utcnow()
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(json_bytes(value))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def git(repo, *args, text=True):
    value = subprocess.check_output(["git", "-C", str(repo), *args], text=text, timeout=60)
    return value.strip() if text else value


def require_repository(repo, allowed_changes=()):
    """Refuse another branch/origin or any unrelated tracked/staged change."""
    if git(repo, "branch", "--show-current") != BRANCH:
        raise ValueError(f"Controller requires branch {BRANCH}")
    if git(repo, "remote", "get-url", "origin") not in {
            f"https://github.com/{REPOSITORY}.git", f"git@github.com:{REPOSITORY}.git"}:
        raise ValueError("Unexpected Git origin; no archive commit or push performed")
    changed = set(filter(None, git(repo, "diff", "HEAD", "--name-only").splitlines()))
    if changed - set(allowed_changes):
        raise ValueError(f"Unrelated tracked changes require investigation: {sorted(changed - set(allowed_changes))}")
    # Staging someone else's new file must not enter this controller's commit.
    staged = set(filter(None, git(repo, "diff", "--cached", "--name-only").splitlines()))
    if staged - set(allowed_changes):
        raise ValueError("Unexpected staged files; no archive commit performed")


def validate_plan(plan, evidence):
    source = plan.get("source", {})
    files = source.get("files_sha256", {})
    tree = hashlib.sha256(json_bytes(files)).hexdigest()
    if (plan.get("schema_version") != "research-campaign-v1" or plan.get("campaign_id") != CAMPAIGN_ID
            or plan.get("seed") != 20261026 or len(plan.get("sessions", [])) != 400
            or len(plan.get("blocks", [])) != 10 or len(plan.get("primary_contrasts", [])) != 26
            or plan.get("output_root") != evidence + "/campaign-results"
            or source.get("git_head") != SOURCE_COMMIT or source.get("git_status")
            or source.get("source_tree_sha256") != SOURCE_TREE or tree != SOURCE_TREE or len(files) != 52
            or plan.get("settings", {}).get("cpus") != 26 or plan["settings"].get("runs") != 3):
        raise ValueError("Replacement plan differs from the authorized fixed C84 campaign")
    return plan


def validate_domain(value, plan):
    if (value.get("schema_version") != "h100-replacement-input-domain-v1"
            or value.get("duckdb_version") != "1.5.6" or not value.get("completed_at_utc")
            or set(value.get("scales", {})) != {"1", "10", "30", "100", "300"}
            or not value.get("tpch_extension_files_sha256")):
        raise ValueError("Incomplete replacement input-domain record")
    for scale, dataset in plan["datasets"].items():
        record = value["scales"][scale]
        if record.get("manifest_sha256") != dataset["manifest_sha256"]:
            raise ValueError("Input-domain record belongs to another physical dataset")
        if record.get("null_counts") != {name: 0 for name in ("o_totalprice", "o_orderdate", "o_orderkey", "o_custkey")}:
            raise ValueError("Replacement data leaves the documented nonnull performance domain")


def registry_entry(campaign, prestart):
    return {"label": "20261027 replacement instance; fresh fixed ten-block campaign",
            "prestart_sha256": prestart, "source_commit": SOURCE_COMMIT, "source_tree_sha256": SOURCE_TREE}


def register_archive(repo, archive, campaign):
    """Add one exact actual identity; preserve all existing registry entries."""
    registry_path = repo / REGISTRY
    registry = json.loads(registry_path.read_bytes())
    if registry.get("schema_version") != "h100-registered-campaign-identities-v1":
        raise ValueError("Unexpected campaign registry schema")
    entry = registry_entry(campaign, sha(archive / "PRESTART_MANIFEST.json"))
    existing = registry["campaigns"].get(campaign)
    if existing is not None and existing != entry:
        raise ValueError("An existing campaign registry entry differs; refusing replacement")
    if existing is None:
        registry["campaigns"][campaign] = entry
        registry_path.write_bytes(json_bytes(registry))
    return entry


STATUS_CODE = """import hashlib,json,pathlib,sys
r=pathlib.Path(sys.argv[1])
def read(name):
 p=r/name
 return p.read_text().strip() if p.is_file() else None
def proc(pid):
 if not pid: return None
 p=pathlib.Path('/proc')/str(pid or '')/'cmdline'
 try: return p.read_bytes().replace(b'\\0',b' ').decode(errors='replace')
 except (FileNotFoundError,ProcessLookupError): return None
out={name:read(name) for name in ['prepare.pid','prepare.phase','prepare.exit','READY_TO_ARCHIVE_PLAN','confirm-driver.pid','confirm-driver.exit','WAITING_FOR_OFFHOST_BACKUP','controller-domain.exit']}
out['prepare_command']=proc(out['prepare.pid']); out['driver_command']=proc(out['confirm-driver.pid'])
if not out['prepare_command']: out['prepare.exit']=read('prepare.exit')
if not out['driver_command']: out['confirm-driver.exit']=read('confirm-driver.exit')
for name in ['CONTROLLER_SUPERVISOR_LAUNCH.json','controller-domain-launch.json','backup-receipts/prestart.json']:
 p=r/name
 out[name]=json.loads(p.read_bytes()) if p.is_file() else None
domain=out['controller-domain-launch.json']; out['domain_command']=proc(domain.get('pid')) if domain else None
if not out['domain_command']: out['controller-domain.exit']=read('controller-domain.exit')
p=r/'campaign.json'
out['plan']=json.loads(p.read_bytes()) if p.is_file() else None
out['campaign_sha256']=hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
out['campaign_sidecar']=read('campaign.json.sha256')
p=r/'INPUT_DOMAIN.json'; out['input_domain']=json.loads(p.read_bytes()) if p.is_file() else None
p=r/'readiness/READINESS.json'; rd=json.loads(p.read_bytes()) if p.is_file() else {}
out['readiness']={'status':rd.get('status'),'outputs':rd.get('successful_outputs_compared'),'errors':rd.get('errors'),'fixtures':len(rd.get('semantic_fixtures',[]))}
print(json.dumps(out))
"""


class Controller:
    def __init__(self, args):
        self.args = args
        self.repo = args.repository.resolve(strict=True)
        spec = importlib.util.spec_from_file_location("replacement_backup", self.repo / "operations/research_backup.py")
        self.backup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.backup)
        self.raw = args.destination.resolve()
        self.raw.mkdir(parents=True, exist_ok=True)
        self.state_path = self.raw / "controller-state.json"
        self.state = {"schema_version": "h100-replacement-local-controller-v1", "host": args.host,
                      "evidence": args.evidence, "status": "RUNNING", "phase": "starting", "started_at_utc": utcnow()}
        self.archive = self.repo / "evidence-research/20261027/h100-confirmatory-replacement-v1"

    def record(self, phase, **values):
        self.state.update(phase=phase, **values)
        write_state(self.state_path, self.state)
        event = {"at_utc": utcnow(), "phase": phase, **values}
        with (self.raw / "controller-events.jsonl").open("ab") as handle:
            handle.write((json.dumps(event, sort_keys=True) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        print(json.dumps(event), flush=True)

    def remote(self, code, *args, data=None, timeout=30):
        command = shlex.join(["python3", "-c", code, *map(str, args)])
        body = self.backup.network_run(self.backup.ssh_prefix(self.args.host, self.args.identity_file) + [command],
                                       input=data, capture=True, timeout=timeout)
        return json.loads(body) if body else None

    def status(self):
        return self.remote(STATUS_CODE, self.args.evidence)

    def transport_retry(self, operation, phase, codes=(255,)):
        """Retry only an unavailable transport, never a failed scientific gate."""
        while True:
            try:
                return operation()
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                if isinstance(exc, subprocess.CalledProcessError) and exc.returncode not in codes:
                    raise
                self.record(phase, transport="retry; no engine action", error=repr(exc))
                time.sleep(self.args.interval_seconds)

    def wait_for(self, predicate, phase):
        previous = None
        while True:
            try:
                status = self.status()
                if status["confirm-driver.exit"] not in (None, "0"):
                    raise RuntimeError("Existing supervisor failed; no restart performed")
                if status["prepare.exit"] not in (None, "0"):
                    raise RuntimeError("Preparation failed; preserve its logs and diagnose")
                if predicate(status):
                    return status
                summary = (status["prepare.phase"], status["WAITING_FOR_OFFHOST_BACKUP"])
                if summary != previous:
                    self.record(phase, prepare_phase=summary[0], waiting_phase=summary[1])
                    previous = summary
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                if isinstance(exc, subprocess.CalledProcessError) and exc.returncode != 255:
                    raise RuntimeError(f"Remote control command failed: {exc.stderr!r}") from exc
                self.record(phase, transport="unavailable; no remote restart", error=repr(exc))
            time.sleep(self.args.interval_seconds)

    def deploy(self):
        require_repository(self.repo)
        files = {}
        for name in TOOLS:
            # Deployment requires committed exact bytes, including this controller.
            committed = git(self.repo, "show", f"HEAD:{name}", text=False)
            if committed != (self.repo / name).read_bytes():
                raise ValueError(f"Operational tool is not committed unchanged: {name}")
            files[Path(name).name] = (self.repo / name).read_text()
        files["H100_REPLACEMENT_20261027.md"] = (self.repo / "docs/H100_REPLACEMENT_20261027.md").read_text()
        body = {"files": files, "git_commit": git(self.repo, "rev-parse", "HEAD")}
        code = """import hashlib,json,pathlib,shutil,sys
root=pathlib.Path(sys.argv[1]); staging=pathlib.Path(sys.argv[2]); value=json.loads(sys.stdin.buffer.read())
if (root/'confirm-driver.started-utc').exists(): raise ValueError('Supervisor already started; do not redeploy live operational code')
staging.mkdir(parents=True,exist_ok=True); operations=root/'operations'; operations.mkdir(exist_ok=True)
for name,text in value['files'].items():
 if pathlib.Path(name).name!=name: raise ValueError('Unsafe deployment filename')
 body=text.encode()
 for target in [staging/name,operations/name]:
  if target.exists():
   if target.read_bytes()!=body: raise ValueError('Existing deployed operational bytes differ')
  else:
   with target.open('xb') as h: h.write(body)
prepare=pathlib.Path('/home/ubuntu/study-h100-research-staging/prepare_h100_replacement.sh')
target=operations/prepare.name
if target.exists() and target.read_bytes()!=prepare.read_bytes(): raise ValueError('Preparation driver changed')
if not target.exists(): shutil.copyfile(prepare,target)
print(json.dumps({'deployed':True,'git_commit':value['git_commit'],'files_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in operations.iterdir() if p.is_file()}}))
"""
        result = self.transport_retry(
            lambda: self.remote(code, self.args.evidence, self.args.staging, data=json_bytes(body)),
            "deployment-transport-retry")
        self.record("operational-tools-deployed", deployment=result)

    def run_domain(self, status):
        if status["input_domain"] is not None:
            validate_domain(status["input_domain"], status["plan"])
            return
        code = """import datetime,json,pathlib,shlex,subprocess,sys
r=pathlib.Path(sys.argv[1]); staging=pathlib.Path(sys.argv[2]); marker=r/'controller-domain-launch.json'
if marker.exists(): print(marker.read_text()); raise SystemExit(0)
script='set -euo pipefail\\nfinish_domain() { code=$?; printf "%s\\\\n" "$code" > '+shlex.quote(str(r/'controller-domain.exit'))+'; }\\ntrap finish_domain EXIT\\n'+shlex.join(['/home/ubuntu/study/.venv/bin/python',str(staging/'record_replacement_inputs.py'),'--data-root','/home/ubuntu/study/data','--output',str(r/'INPUT_DOMAIN.json')])+'\\n'
with marker.open('x') as h: json.dump({'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'pid':None},h)
with (r/'controller-domain.log').open('x') as log:
 p=subprocess.Popen(['bash','-c',script],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
value={'pid':p.pid,'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}; marker.write_text(json.dumps(value)+'\\n'); print(json.dumps(value))
"""
        self.transport_retry(lambda: self.remote(code, self.args.evidence, self.args.staging), "domain-launch-transport-retry")
        self.record("recording-input-domains")
        def done(value):
            if value["controller-domain.exit"] not in (None, "0"):
                raise RuntimeError("Input-domain recording failed; no retry performed")
            if value["controller-domain.exit"] == "0":
                validate_domain(value["input_domain"], value["plan"])
                return True
            marker = value["controller-domain-launch.json"]
            if marker and marker.get("pid") and not value["domain_command"]:
                raise RuntimeError("Input-domain process disappeared without an exit marker; no retry performed")
            return False
        self.wait_for(done, "recording-input-domains")

    def launch_supervisor(self):
        code = """import datetime,json,pathlib,subprocess,sys
r=pathlib.Path(sys.argv[1]); staging=pathlib.Path(sys.argv[2]); marker=r/'CONTROLLER_SUPERVISOR_LAUNCH.json'
if marker.exists(): print(marker.read_text()); raise SystemExit(0)
if (r/'confirm-driver.started-utc').exists(): raise ValueError('An unrecorded supervisor already started; no launch performed')
with marker.open('x') as h: json.dump({'pid':None,'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()},h)
with (r/'confirm-driver.log').open('x') as log:
 p=subprocess.Popen(['bash',str(staging/'run_h100_replacement.sh')],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env={**__import__('os').environ,'RESEARCH_EVIDENCE':str(r),'RESEARCH_BACKUP_ROOT':str(staging/'backups')})
value={'pid':p.pid,'started_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}; marker.write_text(json.dumps(value)+'\\n'); print(json.dumps(value))
"""
        # On an ambiguous SSH result, reconcile the durable marker before any
        # further action. The marker prevents a second launch even after loss.
        try:
            launch = self.remote(code, self.args.evidence, self.args.staging)
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            if isinstance(exc, subprocess.CalledProcessError) and exc.returncode != 255:
                raise RuntimeError(f"Remote supervisor launch was rejected: {exc.stderr!r}") from exc
            status = self.wait_for(lambda value: value["CONTROLLER_SUPERVISOR_LAUNCH.json"] is not None,
                                   "reconciling-supervisor-launch")
            launch = status["CONTROLLER_SUPERVISOR_LAUNCH.json"]
        if not launch.get("pid"):
            raise RuntimeError("Supervisor launch marker is ambiguous; investigate without restarting")
        self.record("supervisor-launch-recorded", supervisor_pid=launch["pid"])

    def archive_and_commit(self, campaign):
        self.transport_retry(lambda: self.backup.pull(self.args.host, self.args.identity_file,
            self.args.staging + "/backups", "prestart", self.raw, campaign), "prestart-transfer-retry",
            codes=(255, 10, 12, 30, 35))
        control = json.loads((self.raw / "prestart.json").read_bytes())
        require_repository(self.repo)
        if not self.archive.exists():
            self.archive.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=self.archive.parent, prefix=".prestart-copy-") as temporary:
                target = Path(temporary) / "archive"
                shutil.copytree(self.raw / "prestart", target)
                self.backup.verify_directory(target, control)
                target.rename(self.archive)
        self.backup.seal_prestart(self.archive, control)
        register_archive(self.repo, self.archive, campaign)
        sys.path.insert(0, str(self.repo))
        from research_reporting.replay import verify_archive
        verified = verify_archive(self.archive)
        validate_plan(verified["plan"], self.args.evidence)
        archive_names = [path.relative_to(self.repo).as_posix() for path in self.archive.rglob("*") if path.is_file()]
        require_repository(self.repo, [REGISTRY, *archive_names])
        git(self.repo, "add", "-f", "--", str(self.archive), REGISTRY)
        if git(self.repo, "diff", "--cached", "--name-only"):
            git(self.repo, "commit", "-qm", "Freeze replacement H100 plan and complete prestart evidence before timing")
        commit = git(self.repo, "rev-parse", "HEAD")
        self.backup.check_git_commit(self.archive, self.repo, commit)
        self.record("prestart-committed-awaiting-private-push", campaign_sha256=campaign,
                    prestart_manifest_sha256=verified["prestart_sha256"], git_commit=commit)
        # No acknowledgement is sent if visibility or push cannot be verified.
        visibility = json.loads(subprocess.check_output(["gh", "repo", "view", REPOSITORY,
                                "--json", "isPrivate,nameWithOwner"], text=True, timeout=30))
        if visibility != {"isPrivate": True, "nameWithOwner": REPOSITORY}:
            raise ValueError("Repository privacy check failed; prestart remains unacknowledged")
        git(self.repo, "push", "origin", BRANCH)
        remote = git(self.repo, "ls-remote", "origin", f"refs/heads/{BRANCH}").split()[0]
        if remote != commit:
            raise ValueError("Private branch does not contain the exact prestart commit at its head")
        return commit

    def acknowledge_and_watch(self, campaign, commit):
        while True:
            try:
                self.backup.acknowledge(self.args.host, self.args.identity_file, self.args.staging + "/backups",
                    self.args.evidence, "prestart", self.raw, self.repo, commit, self.archive, campaign)
                break
            except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
                if isinstance(exc, subprocess.CalledProcessError) and exc.returncode != 255:
                    raise
                # ACK publication is atomic and idempotent for the exact same
                # commit/hash. A reconnect does not launch or rerun any engine.
                self.record("prestart-ack-transport-retry", error=repr(exc))
                time.sleep(self.args.interval_seconds)
        self.record("prestart-acknowledged-watching-block-backups", campaign_sha256=campaign, git_commit=commit)
        self.backup.watch(self.args.host, self.args.identity_file, self.args.evidence,
            self.args.staging + "/backups", self.raw, campaign,
            self.args.staging + "/research_backup.py", self.args.interval_seconds)
        self.record("complete-raw-evidence-preserved-reporting-pending", status="COMPLETE_RAW_BACKUP")

    def run(self):
        require_repository(self.repo)
        for name in TOOLS:
            git(self.repo, "ls-files", "--error-unmatch", name)
        self.record("waiting-for-preparation")
        def prepared(status):
            if status["prepare.exit"] == "0" and status["READY_TO_ARCHIVE_PLAN"]:
                if status["readiness"] != {"status": "PASS", "outputs": 116, "errors": [], "fixtures": 5}:
                    raise ValueError("Preparation readiness gate differs")
                validate_plan(status["plan"], self.args.evidence)
                if status["campaign_sha256"] != status["campaign_sidecar"]:
                    raise ValueError("Remote campaign sidecar differs")
                return True
            if status["prepare.exit"] is None and not status["prepare_command"]:
                raise RuntimeError("Preparation is not live and has no completion marker")
            return False
        status = self.wait_for(prepared, "waiting-for-preparation")
        campaign = status["campaign_sha256"]
        self.record("preparation-passed", campaign_sha256=campaign)
        receipt = status["backup-receipts/prestart.json"]
        if receipt is not None:
            # An explicit controller resume may find the campaign already
            # collecting. Verify the original acknowledgement and continue only
            # its backups; do not wait for prestart again or launch anything.
            control = self.raw / "prestart.json"
            local_receipt = self.raw / "prestart.receipt-reconciled.json"
            local_receipt.write_bytes(json_bytes(receipt))
            self.backup.verify_receipt(control, local_receipt)
            if receipt["campaign_sha256"] != campaign or receipt["prestart_manifest_sha256"] != sha(self.archive / "PRESTART_MANIFEST.json"):
                raise ValueError("Existing prestart receipt does not bind the preserved replacement archive")
            sys.path.insert(0, str(self.repo))
            from research_reporting.replay import verify_archive
            verify_archive(self.archive)
            self.backup.check_git_commit(self.archive, self.repo, receipt["git_commit"])
            self.acknowledge_and_watch(campaign, receipt["git_commit"])
            return
        if status["CONTROLLER_SUPERVISOR_LAUNCH.json"] is None:
            self.deploy()
            self.run_domain(status)
        else:
            validate_domain(status["input_domain"], status["plan"])
        self.launch_supervisor()
        def snapshot_ready(value):
            if value["WAITING_FOR_OFFHOST_BACKUP"] == "prestart":
                return True
            if value["confirm-driver.pid"] and not value["driver_command"]:
                raise RuntimeError("Supervisor disappeared before prestart; no restart performed")
            return False
        self.wait_for(snapshot_ready, "waiting-for-prestart-snapshot")
        commit = self.archive_and_commit(campaign)
        self.acknowledge_and_watch(campaign, commit)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--staging", default="/home/ubuntu/study-h100-research-staging-20261027")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--interval-seconds", type=int, default=60)
    parser.add_argument("--resume-controller", action="store_true")
    args = parser.parse_args()
    if not args.identity_file.is_file() or args.interval_seconds < 10:
        parser.error("An existing identity file and polling interval >=10seconds are required")
    controller = Controller(args)
    controller.backup.remote_path(args.evidence)
    controller.backup.remote_path(args.staging)
    with (controller.raw / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if controller.state_path.exists():
            if not args.resume_controller:
                raise ValueError("Existing controller state requires inspection and explicit --resume-controller")
            old = json.loads(controller.state_path.read_bytes())
            if old.get("host") != args.host or old.get("evidence") != args.evidence:
                raise ValueError("Existing local control state belongs to another host/campaign")
        try:
            controller.run()
        except BaseException as exc:
            controller.record("stopped-attention-required", status="STOPPED_ATTENTION", error=repr(exc))
            raise


if __name__ == "__main__":
    main()
