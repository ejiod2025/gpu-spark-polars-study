"""Execute the real supervisor shell with inert command stubs only."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("fail_block", [None, "block-02"])
def test_real_supervisor_one_fixed_cohort_and_failure_never_restarts(tmp_path, fail_block):
    source = tmp_path / "source"
    source.mkdir()
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "prepare.exit").write_text("0\n")
    (evidence / "READY_TO_ARCHIVE_PLAN").write_text("ready\n")
    commands = tmp_path / "commands.jsonl"
    fake = tmp_path / "python-stub"
    fake.write_text("#!" + sys.executable + "\n" + '''import json,os,pathlib,sys
args=sys.argv[1:]
if args and args[0]=='-B': args=args[1:]
evidence=pathlib.Path(os.environ['RESEARCH_EVIDENCE'])
with pathlib.Path(os.environ['COMMANDS']).open('a') as out:
 out.write(json.dumps({'args':args,'flags':os.environ.get('SPARK_SUBMIT_OPTS'),'banned':{n:os.environ.get(n) for n in ('JAVA_TOOL_OPTIONS','_JAVA_OPTIONS','JDK_JAVA_OPTIONS','SPARK_LAUNCHER_OPTS')}})+'\\n')
if args[0].endswith('jvm_execution_profile.py'):
 phase=args[args.index('--phase')+1]
 with (evidence/'gate-phases').open('a') as out: out.write(phase+'\\n')
elif args[0]=='research_campaign.py' and '--max-sessions' in args:
 phase=(evidence/'confirm-driver.phase').read_text().strip().removeprefix('fixed-sample-')
 if phase==os.environ.get('FAIL_BLOCK'): sys.exit(23)
elif args[0].endswith('research_backup.py') and args[1]=='snapshot':
 phase=args[args.index('--phase')+1]
 control=pathlib.Path(args[args.index('--snapshot-root')+1]);control.mkdir(parents=True,exist_ok=True)
 (control/(phase+'.json')).write_text('{}')
 receipts=evidence/'backup-receipts';receipts.mkdir(exist_ok=True)
 (receipts/(phase+'.json')).write_text('{}')
''')
    fake.chmod(0o755)
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    flock = bin_path / "flock"
    flock.write_text("#!/bin/sh\nexit 0\n")
    flock.chmod(0o755)
    envfile = tmp_path / "env.sh"
    envfile.write_text(f"export PYTHON_GPU_ENV={fake}\n")
    env = {**os.environ, "PATH": str(bin_path) + os.pathsep + os.environ["PATH"], "RESEARCH_SOURCE": str(source),
           "RESEARCH_EVIDENCE": str(evidence), "RESEARCH_ENV": str(envfile), "RESEARCH_BACKUP_ROOT": str(tmp_path / "backups"),
           "RESEARCH_ANALYSIS_PYTHON": str(fake), "COMMANDS": str(commands), "FAIL_BLOCK": fail_block or "",
           "JAVA_TOOL_OPTIONS": "inherited global options must be cleared"}
    result = subprocess.run(["bash", str(REPO / "operations/run_h100_gclocker100.sh")], env=env, capture_output=True, text=True)
    assert result.returncode == (23 if fail_block else 0), result.stderr
    assert (evidence / "confirm-driver.exit").read_text().strip() == str(result.returncode)
    entries = [json.loads(line) for line in commands.read_text().splitlines()]
    sessions = [row for row in entries if row["args"][0] == "research_campaign.py" and "--max-sessions" in row["args"]]
    assert len(sessions) == (2 if fail_block else 10)
    phases = (evidence / "gate-phases").read_text().splitlines()
    assert phases[:2] == ["block-01-before", "block-01-after"]
    assert phases[-1] == ("block-02-before" if fail_block else "final-after")
    assert all(row["flags"] == "-XX:+UnlockDiagnosticVMOptions -XX:GCLockerRetryAllocationCount=100 -XX:+PrintFlagsFinal" for row in entries)
    assert all(all(value is None for value in row["banned"].values()) for row in entries)
    assert (evidence / "ANALYSIS_READY").exists() is (not fail_block)
    before = commands.read_bytes()
    again = subprocess.run(["bash", str(REPO / "operations/run_h100_gclocker100.sh")], env=env, capture_output=True)
    assert again.returncode != 0
    assert commands.read_bytes() == before
