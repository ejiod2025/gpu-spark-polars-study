"""Read-only, exact-host capture for the final H100 retirement guard.

This never reads an API credential, stops a process or mutates the guest.
The output directory must be new. Capture only after publication/preservation.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "instance_id": "8809d4738226458a80a100bb4eb2f906",
    "ip": "68.209.73.247",
    "gpu_uuid": "GPU-aa9e9485-5551-d7be-dfc9-9e433a7284da",
    "gpu_name": "NVIDIA H100 80GB HBM3",
    "driver_version": "580.105.08",
    "api_instance_type": "gpu_1x_h100_sxm5",
    "api_gpu_description": "H100 (80 GB SXM5)",
}
CAMPAIGN = "3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641"
PROBE = r'''
import csv,datetime,hashlib,json,pathlib,subprocess
r=pathlib.Path('/home/ubuntu/study-h100-research-evidence-20261027-gclocker100')
def read(n):
 p=r/n
 return p.read_text().strip() if p.is_file() else None
def command(pid):
 try:return (pathlib.Path('/proc')/str(pid)/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace').strip()
 except (FileNotFoundError,ProcessLookupError,PermissionError):return None
gpu=subprocess.check_output(['nvidia-smi','--query-gpu=uuid,name,driver_version','--format=csv,noheader'],text=True)
gpus=[dict(zip(['gpu_uuid','gpu_name','driver_version'],[s.strip() for s in row])) for row in csv.reader(gpu.splitlines()) if row]
apps=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name,used_memory','--format=csv,noheader'],text=True)
clients=[line.strip() for line in apps.splitlines() if line.strip()]
patterns=('org.apache.spark','spark-submit','pyspark','research_campaign','research_benchmark','contention_runner','launch_contention','study-h100-research','/home/ubuntu/study/')
jobs=[]
for p in pathlib.Path('/proc').iterdir():
 if not p.name.isdigit():continue
 cmd=command(p.name)
 if cmd and any(t in cmd for t in patterns):jobs.append({'pid':int(p.name),'command':cmd})
pid=read('confirm-driver.pid')
out={'captured_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'gpus':gpus,'gpu_compute_processes':clients,'benchmark_processes':jobs,
 'driver_pid':pid,'driver_command':command(pid) if pid else None,
 'driver_exit':read('confirm-driver.exit'),'driver_ended_utc':read('confirm-driver.ended-utc'),
 'campaign_sha256':hashlib.sha256((r/'campaign.json').read_bytes()).hexdigest()}
print(json.dumps(out,sort_keys=True))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.absolute()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new output directory within this repository")
    output.mkdir(parents=True)
    result = subprocess.run([
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "IdentitiesOnly=yes",
        "-i", "/Users/danielejiofor/.ssh/lambda_key22", "ubuntu@68.209.73.247", "python3", "-"
    ], input=PROBE.encode(), capture_output=True, timeout=60)
    (output / "guest-capture.stdout.json").write_bytes(result.stdout)
    (output / "guest-capture.stderr.log").write_bytes(result.stderr)
    result.check_returncode()
    observed = json.loads(result.stdout)
    wanted_gpu = {k: EXPECTED[k] for k in ("gpu_uuid", "gpu_name", "driver_version")}
    if (observed["gpus"] != [wanted_gpu] or observed["campaign_sha256"] != CAMPAIGN
            or observed["driver_exit"] != "0" or not observed["driver_ended_utc"]
            or observed["driver_command"] is not None or observed["benchmark_processes"]
            or observed["gpu_compute_processes"]):
        raise ValueError("Exact H100 identity, successful driver closure or idle checks failed; nothing was stopped")
    log = output / "guest-capture.stdout.json"
    receipt = {
        "schema_version": "h100-offhost-identity-idle-v1", "instance": EXPECTED,
        "campaign_sha256": CAMPAIGN, "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "driver_exit": 0, "driver_alive": False, "benchmark_processes": [], "gpu_compute_processes": [],
        "capture_log_path": str(log.relative_to(ROOT)),
        "capture_log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        "probe_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "api_identity_scope": "Expected fields from earlier exact API read; guard must freshly match them before mutation",
    }
    (output / "IDENTITY_IDLE.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "READ_ONLY_IDLE_CAPTURED", "receipt": str(output / "IDENTITY_IDLE.json")}))


if __name__ == "__main__":
    main()
