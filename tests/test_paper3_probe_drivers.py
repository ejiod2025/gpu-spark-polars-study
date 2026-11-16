"""The probe drivers (tools/paper3_probes/run_lambda_gpu_probe.sh, run_gcp_cpu_probe.sh) and their detached-stage library.

No cloud, no GPU: `ssh`, `scp`, `gcloud` and `lambda.sh` are fakes that run what they are told on this machine in a fake
host home. The fake ssh behaves like the real one in the way that matters here: the call returns only when every process
that holds its stdout and stderr is gone, so a start command that does not detach properly blocks for the whole stage.
Outages (a sleeping laptop) are injected per call: refused connections, and calls that hang until the driver's time limit kills them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import time

import pytest

REPO = Path(__file__).resolve().parents[1]
PROBES = REPO / "tools" / "paper3_probes"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("perl") is None or shutil.which("shasum") is None,
                                reason="bash, perl and shasum are needed")

FAKE_SSH = r'''#!/bin/bash
# fake ssh: ssh [options] [user@]host COMMAND  -> run COMMAND here with HOME=$FAKE_HOST_HOME
n="$FAKE_NET"
echo "$(date +%s) ssh" >> "$n/calls.log"
if [ -f "$n/down_until" ] && [ "$(date +%s)" -lt "$(cat "$n/down_until")" ]; then echo "ssh: connect to host: Connection refused" >&2; exit 255; fi
for f in fail_calls hang_calls; do
  if [ -f "$n/$f" ] && [ "$(cat "$n/$f")" -gt 0 ]; then
    echo $(( $(cat "$n/$f") - 1 )) > "$n/$f"
    if [ "$f" = hang_calls ]; then exec sleep 60; fi          # exec: the process the driver's time limit kills is the one holding the pipes
    echo "ssh: connection timed out" >&2; exit 255
  fi
done
while [ $# -gt 0 ]; do
  case "$1" in -i|-o|-p|-l|-F|-J|-L|-R) shift 2 ;; -*) shift ;; *) break ;; esac
done
shift                                  # the target
cmd="$*"
exec env HOME="$FAKE_HOST_HOME" bash -c "$cmd"
'''

FAKE_SCP = r'''#!/bin/bash
# fake scp: SRC... DST, one side is [user@host:]path on the fake host
n="$FAKE_NET"
echo "$(date +%s) scp" >> "$n/calls.log"
if [ -f "$n/down_until" ] && [ "$(date +%s)" -lt "$(cat "$n/down_until")" ]; then echo "scp: Connection refused" >&2; exit 255; fi
if [ -f "$n/scp_fail_calls" ] && [ "$(cat "$n/scp_fail_calls")" -gt 0 ]; then echo $(( $(cat "$n/scp_fail_calls") - 1 )) > "$n/scp_fail_calls"; echo "scp: lost connection" >&2; exit 255; fi
args=()
while [ $# -gt 0 ]; do
  case "$1" in -i|-o|-P|-F|-J|-l) shift 2 ;; -*) shift ;; *) args+=("$1"); shift ;; esac
done
dst="${args[$(( ${#args[@]} - 1 ))]}"
unset 'args[$(( ${#args[@]} - 1 ))]'
host_path() { local p=$1; p=${p#*:}; case "$p" in "~/"*) p=${p#"~/"} ;; "~") p="" ;; esac; case "$p" in /*) echo "$p" ;; *) echo "$FAKE_HOST_HOME/$p" ;; esac; }
if [[ "$dst" == *:* && "$dst" != /* ]]; then
  for s in "${args[@]}"; do cp -R "$s" "$(host_path "$dst")" || exit 1; done
else
  for s in "${args[@]}"; do cp -R "$(host_path "$s")" "$dst" || { echo "scp: $s: No such file or directory" >&2; exit 1; }; done
fi
'''

FAKE_GCLOUD = r'''#!/bin/bash
# fake gcloud: compute instances create/delete/list, compute ssh, compute scp
n="$FAKE_NET"
state="$FAKE_NET/instance"
case "$1 $2 $3" in
  "compute instances create") touch "$state"; echo "created" ; exit 0 ;;
  "compute instances delete") rm -f "$state"; exit 0 ;;
  "compute instances list") [ -f "$state" ] && echo "${FAKE_INSTANCE_NAME:-p3-fake}"; exit 0 ;;
  "compute ssh "*|"compute scp "*) ;;
  *) echo "fake gcloud: unsupported $*" >&2; exit 2 ;;
esac
sub=$2
shift 2
if [ "$sub" = ssh ]; then
  echo "$(date +%s) ssh" >> "$n/calls.log"
  if [ -f "$n/down_until" ] && [ "$(date +%s)" -lt "$(cat "$n/down_until")" ]; then echo "ssh: Connection refused" >&2; exit 255; fi
  cmd=""
  while [ $# -gt 0 ]; do
    case "$1" in --command) cmd="$2"; shift 2 ;; *) shift ;; esac
  done
  exec env HOME="$FAKE_HOST_HOME" bash -c "$cmd"
fi
echo "$(date +%s) scp" >> "$n/calls.log"
args=()
while [ $# -gt 0 ]; do case "$1" in --*) shift ;; *) args+=("$1"); shift ;; esac; done
dst="${args[$(( ${#args[@]} - 1 ))]}"
unset 'args[$(( ${#args[@]} - 1 ))]'
host_path() { local p=${1#*:}; case "$p" in "~/"*) p=${p#"~/"} ;; esac; case "$p" in /*) echo "$p" ;; *) echo "$FAKE_HOST_HOME/$p" ;; esac; }
if [[ "$dst" == *:* && "$dst" != /* ]]; then
  for s in "${args[@]}"; do cp -R "$s" "$(host_path "$dst")" || exit 1; done
else
  for s in "${args[@]}"; do cp -R "$(host_path "$s")" "$dst" || exit 1; done
fi
'''

FAKE_SUDO = '#!/bin/bash\nexec "$@"\n'


@pytest.fixture
def net(tmp_path, monkeypatch):
    """A fake host (its home directory), fake ssh/scp/gcloud/sudo first on PATH, and the knobs that make the network misbehave."""
    host = tmp_path / "host"
    netdir = tmp_path / "net"
    bin_dir = tmp_path / "bin"
    for directory in (host, netdir, bin_dir):
        directory.mkdir()
    for name, text in (("ssh", FAKE_SSH), ("scp", FAKE_SCP), ("gcloud", FAKE_GCLOUD), ("sudo", FAKE_SUDO)):
        (bin_dir / name).write_text(text)
        (bin_dir / name).chmod(0o755)
    shutil.copy(PROBES / "p3_detach.sh", host / "p3_detach.sh")
    (host / "p3out").mkdir()
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_HOST_HOME": str(host), "FAKE_NET": str(netdir), "HOME": str(tmp_path / "mac-home"),
           "P3_NO_CAFFEINATE": "1", "P3_POLL_SECONDS": "0.3", "P3_RETRY_PAUSE": "0.1", "P3_SSH_BOUND": "3", "P3_SCP_BOUND": "5",
           "P3_STAGE_TIMEOUT": "60", "LC_ALL": "C"}
    (tmp_path / "mac-home" / ".ssh").mkdir(parents=True)
    (tmp_path / "mac-home" / ".ssh" / "lambda_key22").write_text("fake")
    yield type("Net", (), {"host": host, "net": netdir, "env": env, "tmp": tmp_path, "bin": bin_dir})
    subprocess.run(["pkill", "-f", str(tmp_path)], capture_output=True)       # any stage still running on the fake host


LIB_PREAMBLE = f'''
set -uo pipefail
p3_log() {{ echo "LOG $*"; }}
. "{PROBES}/p3_stage_lib.sh"
P3_SSH=(ssh -o ConnectTimeout=5 ubuntu@10.0.0.1)
P3_SCP_FROM=(scp -o ConnectTimeout=5)
P3_SCP_TO=(scp -o ConnectTimeout=5)
P3_REMOTE_PREFIX="ubuntu@10.0.0.1:"
'''


def run_lib(net, body, timeout=60, **extra):
    env = {**net.env, **{k: str(v) for k, v in extra.items()}}
    started = time.monotonic()
    done = subprocess.run([BASH, "-c", LIB_PREAMBLE + textwrap.dedent(body)], capture_output=True, text=True, env=env, timeout=timeout)
    done.elapsed = time.monotonic() - started
    return done


def status(net, stage):
    done = subprocess.run([BASH, str(net.host / "p3_detach.sh"), "STATUS", stage], capture_output=True, text=True, env={**net.env, "HOME": str(net.host)})
    return dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)


# ---- the detached start ---------------------------------------------------------------------------------------------------

def test_the_start_call_returns_at_once_while_the_stage_keeps_running(net):
    done = run_lib(net, '''
        t0=$(date +%s)
        p3_stage_start demo bash -c 'sleep 6; echo finished'
        echo "START_RETURNED_AFTER $(( $(date +%s) - t0 ))"
    ''')
    assert done.returncode == 0, done.stderr
    assert "stage demo: started demo" in done.stdout
    returned = int(re.search(r"START_RETURNED_AFTER (\d+)", done.stdout).group(1))
    assert done.elapsed < 4 and returned <= 2                          # the stage needs 6 s; the call that started it did not wait for it
    state = status(net, "demo")
    assert state["STARTED"] == "1" and state["RUNNING"] == "1" and state["RC"] == ""
    deadline = time.monotonic() + 15
    while status(net, "demo")["RC"] == "" and time.monotonic() < deadline:
        time.sleep(0.2)
    assert status(net, "demo")["RC"] == "0" and (net.host / "p3out" / "demo.log").read_text().strip() == "finished"
    assert not (net.host / "p3out" / "demo.rc.tmp").exists()


def test_the_pattern_the_gcp_driver_used_blocks_for_the_whole_stage(net):
    """Regression evidence for the bug: `a && b && nohup c > /dev/null 2>&1 < /dev/null & disown; echo started` backgrounds the
    whole AND-list, whose subshell still holds the ssh channel's stdout, so the call lasts as long as the stage."""
    old = 'cd "$HOME" && rm -rf x && nohup bash -c "sleep 3; echo hi" > /dev/null 2>&1 < /dev/null & disown; echo started'
    started = time.monotonic()
    done = subprocess.run([str(net.bin / "ssh"), "ubuntu@10.0.0.1", old], capture_output=True, text=True, env=net.env, timeout=60)
    assert "started" in done.stdout and time.monotonic() - started >= 2.8
    new = 'bash "$HOME/p3_detach.sh" START oldpattern bash -c "sleep 3; echo hi"'
    started = time.monotonic()
    done = subprocess.run([str(net.bin / "ssh"), "ubuntu@10.0.0.1", new], capture_output=True, text=True, env=net.env, timeout=60)
    assert "started oldpattern" in done.stdout and time.monotonic() - started < 2.0


def test_starting_a_stage_twice_runs_it_once(net):
    done = run_lib(net, '''
        p3_stage_start once bash -c 'echo run >> "$HOME/runs.txt"; sleep 1'
        p3_stage_start once bash -c 'echo run >> "$HOME/runs.txt"; sleep 1'
        p3_stage_wait once && echo "RC=$P3_STAGE_RC"
    ''')
    assert "stage once: started once" in done.stdout and "stage once: already-started once" in done.stdout and "RC=0" in done.stdout
    assert (net.host / "runs.txt").read_text().splitlines() == ["run"]


@pytest.mark.parametrize("command, code", [("bash -c 'exit 3'", 3), ("true", 0), ("no-such-command-here", 127)])
def test_the_exit_code_of_the_stage_is_what_the_poll_returns(net, command, code):
    done = run_lib(net, f'''
        p3_stage_start code {command}
        p3_stage_wait code && echo "RC=$P3_STAGE_RC"
    ''')
    assert f"RC={code}" in done.stdout, done.stdout + done.stderr


def test_arguments_with_spaces_and_quotes_reach_the_command_intact(net):
    done = run_lib(net, r'''
        p3_stage_start args bash -c 'printf "%s|" "$@"' _ 'two words' "it's" '$HOME' '*'
        p3_stage_wait args && cat "$FAKE_HOST_HOME/p3out/args.log"
    ''')
    assert "two words|it's|$HOME|*|" in done.stdout


def test_a_start_that_fails_to_reach_the_host_is_retried_until_it_does(net):
    (net.net / "fail_calls").write_text("3")
    done = run_lib(net, '''
        p3_stage_start retry true && echo START_OK
        p3_stage_wait retry && echo "RC=$P3_STAGE_RC"
    ''')
    assert done.stdout.count("start call attempt") == 3 and "START_OK" in done.stdout and "RC=0" in done.stdout


def test_a_start_that_never_reaches_the_host_gives_up_after_its_budget(net):
    (net.net / "down_until").write_text(str(int(time.time()) + 600))
    done = run_lib(net, "p3_stage_start never true || echo START_FAILED", P3_START_RETRIES=3, P3_RETRY_PAUSE=0.05)
    assert "START_FAILED" in done.stdout and done.stdout.count("start call attempt") == 3


# ---- polling ---------------------------------------------------------------------------------------------------------------

def test_a_stage_is_finished_exactly_when_its_exit_code_file_exists(net):
    done = run_lib(net, '''
        p3_stage_start slow bash -c 'sleep 3'
        t0=$(date +%s)
        p3_stage_wait slow && echo "RC=$P3_STAGE_RC AFTER=$(( $(date +%s) - t0 ))"
    ''')
    after = int(re.search(r"AFTER=(\d+)", done.stdout).group(1))
    assert "RC=0" in done.stdout and 2 <= after <= 6                    # not before the stage ended, and without waiting past it


def test_failed_polls_are_retried_and_never_fatal(net):
    done = run_lib(net, '''
        p3_stage_start flaky bash -c 'sleep 2'
        echo 5 > "$FAKE_NET/fail_calls"
        p3_stage_wait flaky && echo "RC=$P3_STAGE_RC"
    ''')
    assert "RC=0" in done.stdout
    assert "poll failed (rc=255, 1 in a row" in done.stdout and "polling works again after 5 failed poll(s)" in done.stdout


def test_a_laptop_that_sleeps_mid_stage_costs_polls_not_the_stage(net):
    """Outage: refused connections for 3 s, then one call that hangs until the time limit kills it, then normal; the stage on the
    host runs meanwhile and the driver ends up with its exit code."""
    done = run_lib(net, '''
        p3_stage_start napping bash -c 'sleep 5; echo done'
        echo $(( $(date +%s) + 3 )) > "$FAKE_NET/down_until"
        t0=$(date +%s)
        # the first call after the outage hangs (a stale TCP session): it must be killed by the bound, not waited for
        ( while [ "$(date +%s)" -lt "$(cat "$FAKE_NET/down_until")" ]; do sleep 0.1; done; echo 1 > "$FAKE_NET/hang_calls" ) &
        p3_stage_wait napping && echo "RC=$P3_STAGE_RC AFTER=$(( $(date +%s) - t0 ))"
    ''', P3_SSH_BOUND=1)
    after = int(re.search(r"AFTER=(\d+)", done.stdout).group(1))
    assert "RC=0" in done.stdout and after < 25
    assert "poll failed (rc=255" in done.stdout and "poll failed (rc=124" in done.stdout          # refused, then killed by the bound
    assert "polling works again" in done.stdout
    assert (net.host / "p3out" / "napping.log").read_text().strip() == "done"


def test_a_long_gap_between_polls_is_reported_as_a_suspended_mac(net):
    done = run_lib(net, '''
        p3_stage_start gap bash -c 'sleep 4'
        P3_SLEEP() { sleep 1; }
        p3_gapped_sleep() { if [ -z "${SLEPT:-}" ]; then SLEPT=1; /bin/sleep 3; else /bin/sleep 0.2; fi; }
        P3_SLEEP=p3_gapped_sleep
        P3_POLL_SECONDS=0.2
        p3_stage_wait gap && echo "RC=$P3_STAGE_RC"
    ''', P3_SSH_BOUND=1)
    assert "RC=0" in done.stdout and re.search(r"stage gap: \d+ s since the previous poll \(the Mac was asleep or suspended\)", done.stdout)


def test_the_polling_budget_ends_in_a_timeout_state_not_a_hang(net):
    done = run_lib(net, '''
        p3_stage_start endless bash -c 'sleep 8'
        p3_stage_wait endless || echo "STATE=$P3_STAGE_STATE"
    ''', P3_STAGE_TIMEOUT=1, P3_POLL_SECONDS=0.2)
    assert "STATE=timeout" in done.stdout and done.elapsed < 15


def test_a_stage_whose_process_vanished_without_an_exit_code_is_called_died(net):
    done = run_lib(net, f'''
        p3_stage_start ghost bash -c 'sleep 30'
        pkill -9 -f "p3_detach.sh RUN ghost" ; pkill -9 -f "sleep 30"
        p3_stage_wait ghost || echo "STATE=$P3_STAGE_STATE"
    ''')
    assert "STATE=died" in done.stdout and "no exit code file and no running process on three consecutive polls" in done.stdout


def test_a_stage_that_was_never_started_on_the_host_is_called_not_started(net):
    done = run_lib(net, 'p3_stage_wait phantom || echo "STATE=$P3_STAGE_STATE"')
    assert "STATE=not-started" in done.stdout


def test_polls_that_keep_failing_end_when_the_provider_says_the_host_is_gone(net):
    (net.net / "down_until").write_text(str(int(time.time()) + 600))
    done = run_lib(net, '''
        p3_host_alive() { return 1; }
        p3_stage_wait lost || echo "STATE=$P3_STAGE_STATE"
    ''', P3_MAX_FAILED_POLLS=3, P3_POLL_SECONDS=0.1)
    assert "STATE=host-gone" in done.stdout and "no longer lists the host" in done.stdout
    still = run_lib(net, '''
        p3_host_alive() { return 0; }
        p3_stage_wait lost || echo "STATE=$P3_STAGE_STATE"
    ''', P3_MAX_FAILED_POLLS=3, P3_POLL_SECONDS=0.1, P3_STAGE_TIMEOUT=2, P3_RETRY_PAUSE=0.05)
    assert "STATE=timeout" in still.stdout                              # alive but unreachable: wait out the budget, not give up early


# ---- the time limit and the copies ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("no_perl", [False, True])
def test_bounded_kills_a_hung_command_and_leaves_nothing_behind(net, no_perl):
    extra = {"P3_NO_PERL": "1"} if no_perl else {}
    done = run_lib(net, '''
        p3_bounded 1 bash -c 'sleep 20'; echo "RC=$?"
        p3_bounded 5 bash -c 'echo ok; exit 7'; echo "RC=$?"
        p3_bounded 5 true; echo "RC=$?"
    ''', P3_SLEEP="sleep", **extra)
    assert done.stdout.split() == ["RC=124", "ok", "RC=7", "RC=0"] and done.elapsed < 8


def test_fetch_and_listing_are_bounded_and_retried(net):
    (net.host / "p3out" / "a-1.csv").write_text("1\n")
    (net.host / "p3out" / "a-2.csv").write_text("2\n")
    (net.host / "p3out" / "b.json").write_text("{}\n")
    out = net.tmp / "fetched"
    out.mkdir()
    (net.net / "scp_fail_calls").write_text("2")
    done = run_lib(net, f'''
        p3_remote_names "a-*.csv" "b.json" "missing-*.json"
        echo "LIST_RC=$?"
        p3_fetch p3out/a-1.csv "{out}/" && echo FETCH_OK
        p3_fetch p3out/nothing "{out}/" || echo FETCH_FAILED
    ''', P3_RETRIES=3)
    assert done.stdout.count("\n") >= 3 and "a-1.csv\na-2.csv\nb.json\nLIST_RC=0" in done.stdout          # unmatched patterns are not an error
    assert "FETCH_OK" in done.stdout and done.stdout.count("fetch of p3out/a-1.csv, attempt") == 2 and "FETCH_FAILED" in done.stdout
    assert (out / "a-1.csv").read_text() == "1\n"


def test_listing_gives_up_with_an_error_only_when_ssh_itself_keeps_failing(net):
    (net.net / "down_until").write_text(str(int(time.time()) + 600))
    done = run_lib(net, 'p3_remote_names "*.json" || echo LIST_FAILED', P3_RETRIES=2, P3_RETRY_PAUSE=0.05)
    assert "LIST_FAILED" in done.stdout


# ---- the whole Lambda driver, against fakes -----------------------------------------------------------------------------------

FAKE_LAMBDA = r'''#!/bin/bash
# fake lambda.sh: launch / wait / list / terminate for one fake instance
state=/tmp/p3probe
case "$1" in
  launch) echo "$FAKE_INSTANCE_ID"; echo "p3-probe-$2-owner-ejiod-expires-fake" > "$state/$FAKE_INSTANCE_ID.name"; echo "launched" >&2 ;;
  wait) echo 10.0.0.1 > "$state/$2.ip"; echo "ssh ready" ;;
  list) [ -f "$FAKE_NET/terminated" ] || printf '%s\tp3-probe\tactive\tgpu\tregion\n' "$FAKE_INSTANCE_ID" ;;
  terminate) touch "$FAKE_NET/terminated"; echo "[]" ;;
esac
'''

FAKE_P3_GPU_PROBES = r'''#!/usr/bin/env python3
"""Stand-in for p3_gpu_probes.py on the fake host: each step takes a moment and writes its files."""
import json, sys, time
from pathlib import Path
step = sys.argv[1]
out = Path(sys.argv[sys.argv.index("--out") + 1])
out.mkdir(parents=True, exist_ok=True)
Path("argv-" + step + ".json").write_text(json.dumps(sys.argv[1:]))
time.sleep(0.3 if "--g8-pulses" in sys.argv else float(__import__("os").environ.get("FAKE_STEP_SECONDS", "0.8")))   # the smoke test is short
if step == "bad":
    sys.exit(4)
files = {"p5-gpu": ["p5-gpu.json", "p5-gpu-idle-poller-1.csv"], "semantics": ["semantics.json", "semantics-50ms.csv", "semantics-scope-20ms.csv"],
         "p6": ["p6.json", "p6-power-20ms.csv"], "g8": ["g8.json", "g8-gpu.csv"]}[step]
for name in files:
    (out / name).write_text(json.dumps({"step": step, "file": name}) + "\n")
print("step", step, "done", flush=True)
if "--fail-step" in sys.argv or (step == "p5-gpu" and __import__("os").environ.get("FAKE_P5_FAILS")):
    sys.exit(2)
'''

FAKE_PROBE_POWER = r'''#!/usr/bin/env python3
import sys, json
from pathlib import Path
Path(sys.argv[sys.argv.index("--output") + 1]).write_text(json.dumps({"sources": []}) + "\n")
print("{}")
'''


@pytest.fixture
def lambda_repo(net):
    """A copy of the driver and its helpers in a throwaway repo layout, with fake lambda.sh, watchdog and probe scripts."""
    root = net.tmp / "repo"
    probes = root / "tools" / "paper3_probes"
    probes.mkdir(parents=True)
    (root / "docs" / "paper3-evidence" / "probes").mkdir(parents=True)
    for name in ("run_lambda_gpu_probe.sh", "p3_stage_lib.sh", "p3_detach.sh"):
        shutil.copy(PROBES / name, probes / name)
    for name, text in (("lambda.sh", FAKE_LAMBDA), ("lambda_watchdog.sh", "#!/bin/bash\nsleep 0.1\n"), ("p3_gpu_probes.py", FAKE_P3_GPU_PROBES)):
        (probes / name).write_text(text)
    (root / "power_telemetry.py").write_text("# fake\n")
    (root / "probe_power.py").write_text(FAKE_PROBE_POWER)
    for path in probes.glob("*.sh"):
        path.chmod(0o755)
    instance = f"fake{os.getpid()}{int(time.time())}"
    Path("/tmp/p3probe").mkdir(exist_ok=True)
    env = {**net.env, "FAKE_INSTANCE_ID": instance, "P3_POLL_SECONDS": "0.3", "P3_STAGE_TIMEOUT": "60"}
    yield type("Repo", (), {"root": root, "probes": probes, "env": env, "evidence": root / "docs" / "paper3-evidence" / "probes", "instance": instance})
    for suffix in (".name", ".ip"):
        Path(f"/tmp/p3probe/{instance}{suffix}").unlink(missing_ok=True)


def run_lambda_driver(repo, *args, timeout=180, **extra_env):
    return subprocess.run([BASH, str(repo.probes / "run_lambda_gpu_probe.sh"), "b200", "gpu_1x_b200_sxm6", "region-1", "2", "60", *args],
                          capture_output=True, text=True, env={**repo.env, **{k: str(v) for k, v in extra_env.items()}}, timeout=timeout)


def published_log(repo):
    return next(repo.evidence.glob("lambda-b200-*-run.log")).read_text()


def test_the_lambda_driver_runs_every_stage_detached_and_collects_everything(net, lambda_repo):
    done = run_lambda_driver(lambda_repo)
    assert done.returncode == 0, done.stdout + done.stderr
    log = published_log(lambda_repo)
    assert "pre-flight smoke test (2 pulses, canary only, NOT a result) exit=0" in log
    assert "stage exit codes: p5-gpu=0 semantics=0 p6=0 g8=0 sources=0" in log
    for step in ("p5-gpu", "semantics", "p6", "g8", "sources"):
        assert f"step={step} exit=0" in log
    names = sorted(p.name.split("-", 3)[3] for p in lambda_repo.evidence.glob("lambda-b200-*") if "run.log" not in p.name and "SHA256" not in p.name)
    assert names == ["g8-gpu.csv", "g8.json", "p5-gpu-idle-poller-1.csv", "p5-gpu.json", "p6-power-20ms.csv", "p6.json",
                     "semantics-50ms.csv", "semantics-scope-20ms.csv", "semantics.json", "sources.json"]
    sums = next(lambda_repo.evidence.glob("*SHA256SUMS.txt")).read_text().splitlines()
    assert len(sums) == 10 and all(re.fullmatch(r"[0-9a-f]{64}  lambda-b200-\S+  \d+B", line) for line in sums)
    for line in sums:                                                       # the recorded hash is the file's
        digest, name = line.split()[0], line.split()[1]
        assert subprocess.run(["shasum", "-a", "256", str(lambda_repo.evidence / name)], capture_output=True, text=True).stdout.split()[0] == digest
    assert "confirmed terminated" in log and (net.net / "terminated").exists()
    assert "<ip>" not in log or "10.0.0.1" not in log                       # the published log never shows the address
    assert (net.host / "p3out" / "p6.rc").read_text().strip() == "0" and (net.host / "p3out" / "p6.log").read_text().strip() == "step p6 done"
    assert "tail of the host's p6.log" in next(lambda_repo.probes.glob("run-b200-*.log")).read_text()


def test_the_lambda_driver_survives_a_network_outage_in_the_middle_of_a_stage(net, lambda_repo):
    """The B200 failure: ssh exit 255 for a long stretch. Here the stage p6 runs on the host while every ssh and scp call is
    refused for a few seconds and one call hangs; the driver must still end with every stage collected and the host terminated."""
    outage = '''
        ( while [ ! -f "$FAKE_HOST_HOME/p3out/p6.log" ]; do sleep 0.1; done
          echo $(( $(date +%s) + 3 )) > "$FAKE_NET/down_until"
          sleep 3.5; echo 1 > "$FAKE_NET/hang_calls" ) &
    '''
    wrapper = net.tmp / "outage_then_driver.sh"
    wrapper.write_text("#!/bin/bash\n" + textwrap.dedent(outage) + f'exec "{BASH}" "{lambda_repo.probes}/run_lambda_gpu_probe.sh" b200 gpu_1x_b200_sxm6 region-1 2 60\n')
    done = subprocess.run([BASH, str(wrapper)], capture_output=True, text=True, timeout=240,
                          env={**lambda_repo.env, "FAKE_STEP_SECONDS": "5", "P3_SSH_BOUND": "2", "P3_RETRY_PAUSE": "0.3"})
    assert done.returncode == 0, done.stdout + done.stderr
    log = published_log(lambda_repo)
    assert "stage exit codes: p5-gpu=0 semantics=0 p6=0 g8=0 sources=0" in log
    assert re.search(r"poll failed \(rc=255", log) or re.search(r"attempt \d+/\d+ failed", log)      # the outage was met and survived
    assert len(next(lambda_repo.evidence.glob("*SHA256SUMS.txt")).read_text().splitlines()) == 10
    assert "confirmed terminated" in log


def test_the_lambda_driver_passes_p3_steps_and_p3_probe_args_to_the_stage(net, lambda_repo):
    done = run_lambda_driver(lambda_repo, P3_STEPS="p6", P3_PROBE_ARGS="--p6-field module.power.draw.instant")
    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads((net.host / "argv-p6.json").read_text()) == ["p6", "--out", "p3out", "--p6-field", "module.power.draw.instant"]
    log = published_log(lambda_repo)
    assert "stages: [p6] extra args: [--p6-field module.power.draw.instant]" in log and "stage exit codes: p6=0" in log
    assert not (net.host / "argv-g8.json").exists() or json.loads((net.host / "argv-g8.json").read_text())[:2] == ["g8", "--out"]   # only the smoke test ran g8


def test_a_failing_stage_is_recorded_with_its_exit_code_and_the_battery_goes_on(net, lambda_repo):
    done = run_lambda_driver(lambda_repo, FAKE_P5_FAILS="1")
    assert done.returncode == 0, done.stdout + done.stderr
    log = published_log(lambda_repo)
    assert "step=p5-gpu exit=2" in log and "step=semantics exit=0" in log and "stage exit codes: p5-gpu=2 semantics=0 p6=0 g8=0 sources=0" in log
    assert "collected [p5-gpu] " in log                                    # what the failed stage did write is still collected


def test_a_failing_smoke_test_skips_the_paid_battery(net, lambda_repo):
    (lambda_repo.probes / "p3_gpu_probes.py").write_text(FAKE_P3_GPU_PROBES.replace('step = sys.argv[1]', 'step = sys.argv[1]\nif step == "g8" and "--g8-pulses" in sys.argv:\n    sys.exit(9)'))
    done = run_lambda_driver(lambda_repo)
    assert done.returncode == 1
    log = published_log(lambda_repo)
    assert "smoke test (2 pulses, canary only, NOT a result) exit=9" in log and "skipping the paid battery" in log
    assert "step=" not in log and "confirmed terminated" in log


def test_the_unreachable_host_ends_the_run_with_a_termination_not_a_hang(net, lambda_repo):
    (net.net / "down_until").write_text(str(int(time.time()) + 3600))
    done = run_lambda_driver(lambda_repo, P3_RETRIES=2, P3_RETRY_PAUSE=0.1, timeout=120)
    assert done.returncode == 1
    log = published_log(lambda_repo)
    assert "initial scp of scripts failed after" in log and "confirmed terminated" in log


# ---- the GCP driver --------------------------------------------------------------------------------------------------------

FAKE_P3_CPU_PROBES = r'''#!/usr/bin/env python3
import sys, time, json
from pathlib import Path
out = Path(sys.argv[sys.argv.index("--out") + 1])
out.mkdir(parents=True, exist_ok=True)
print("p6-cpu: expected runtime about 1 min", file=sys.stderr, flush=True)
time.sleep(5)
for name in ("p6-cpu.json", "p6-cpu-power.csv", "p6-cpu-power-sources.json"):
    (out / name).write_text(json.dumps({"file": name}) + "\n")
print(json.dumps({"step": "p6-cpu"}))
'''


@pytest.fixture
def gcp_repo(net):
    root = net.tmp / "gcprepo"
    probes = root / "tools" / "paper3_probes"
    probes.mkdir(parents=True)
    (root / "docs" / "paper3-evidence" / "probes").mkdir(parents=True)
    for name in ("run_gcp_cpu_probe.sh", "p3_stage_lib.sh", "p3_detach.sh"):
        shutil.copy(PROBES / name, probes / name)
    (probes / "gcp_watchdog.sh").write_text("#!/bin/bash\nsleep 0.1\n")
    (probes / "p3_cpu_probes.py").write_text(FAKE_P3_CPU_PROBES)
    for path in probes.glob("*.sh"):
        path.chmod(0o755)
    git = lambda *args: subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args], capture_output=True, text=True, check=True)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-q", "-m", "fake")
    env = {**net.env, "FAKE_INSTANCE_NAME": "p3-fake", "P3_POLL_SECONDS": "0.5", "P3_STAGE_TIMEOUT": "120"}
    return type("Repo", (), {"root": root, "probes": probes, "env": env, "evidence": root / "docs" / "paper3-evidence" / "probes"})


def test_the_gcp_driver_starts_the_probe_detached_and_polls_for_its_exit_code(net, gcp_repo):
    done = subprocess.run([BASH, str(gcp_repo.probes / "run_gcp_cpu_probe.sh"), "us-east4-a"], capture_output=True, text=True, env=gcp_repo.env, timeout=240)
    assert done.returncode == 0, done.stdout + done.stderr
    log = next(gcp_repo.evidence.glob("gcp-*-run.log")).read_text()
    stamps = {m.group(2): m.group(1) for m in re.finditer(r"^(\S+) (p6-cpu started detached on the host|probe exit marker: \S+)$", log, re.M)}
    assert "p6-cpu started detached on the host" in stamps and "probe exit marker: 0" in stamps
    # the start call returned at once: the stage needs 5 s, so the marker is found well after the start line (the old driver
    # logged the start after the stage had finished)
    from datetime import datetime
    parse = lambda text: datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ")
    assert (parse(stamps["probe exit marker: 0"]) - parse(stamps["p6-cpu started detached on the host"])).total_seconds() >= 3
    collected = sorted(p.name for p in gcp_repo.evidence.glob("gcp-c3-highmem-192-metal-*") if "run.log" not in p.name and "SHA256" not in p.name)
    assert [n.split("-", 4)[4] for n in collected] == ["p6-cpu-power-sources.json", "p6-cpu-power.csv", "p6-cpu.json", "p6-cpu.log"] or len(collected) == 4
    assert "confirmed deleted" in log and not (net.net / "instance").exists()
    assert (net.host / "p6cpu" / "p6-cpu.json").exists() and (net.host / "p3out" / "p6cpu.rc").read_text().strip() == "0"
    assert "expected runtime" in (net.host / "p3out" / "p6cpu.log").read_text()


def test_the_gcp_driver_survives_an_outage_while_the_probe_runs(net, gcp_repo):
    wrapper = net.tmp / "gcp_outage.sh"
    wrapper.write_text("#!/bin/bash\n" + textwrap.dedent('''
        ( while [ ! -f "$FAKE_HOST_HOME/p3out/p6cpu.log" ]; do sleep 0.1; done
          echo $(( $(date +%s) + 3 )) > "$FAKE_NET/down_until" ) &
    ''') + f'exec "{BASH}" "{gcp_repo.probes}/run_gcp_cpu_probe.sh" us-east4-a\n')
    done = subprocess.run([BASH, str(wrapper)], capture_output=True, text=True, timeout=240, env={**gcp_repo.env, "P3_RETRY_PAUSE": "0.3", "P3_SSH_BOUND": "5"})
    assert done.returncode == 0, done.stdout + done.stderr
    log = next(gcp_repo.evidence.glob("gcp-*-run.log")).read_text()
    assert "probe exit marker: 0" in log and "confirmed deleted" in log
    assert len(list(gcp_repo.evidence.glob("gcp-c3-highmem-192-metal-*p6-cpu*"))) >= 4


def test_the_lambda_driver_stops_the_battery_when_the_provider_says_the_host_is_gone(net, lambda_repo):
    """Every ssh call fails from the middle of the first stage on and the API no longer lists the instance (terminated by its
    lease, the watchdog or the console): the driver must stop waiting out the polling budget, skip the other stages and end."""
    wrapper = net.tmp / "gone_then_driver.sh"
    wrapper.write_text("#!/bin/bash\n" + textwrap.dedent('''
        ( while [ ! -f "$FAKE_HOST_HOME/p3out/p5-gpu.log" ]; do sleep 0.1; done
          echo $(( $(date +%s) + 3600 )) > "$FAKE_NET/down_until"; touch "$FAKE_NET/terminated" ) &
    ''') + f'exec "{BASH}" "{lambda_repo.probes}/run_lambda_gpu_probe.sh" b200 gpu_1x_b200_sxm6 region-1 2 60\n')
    done = subprocess.run([BASH, str(wrapper)], capture_output=True, text=True, timeout=240,
                          env={**lambda_repo.env, "FAKE_STEP_SECONDS": "30", "P3_MAX_FAILED_POLLS": "3", "P3_SSH_BOUND": "2", "P3_RETRY_PAUSE": "0.1",
                               "P3_POLL_SECONDS": "0.2"})
    assert done.returncode == 0, done.stdout + done.stderr
    log = published_log(lambda_repo)
    assert "step=p5-gpu exit=host-gone" in log and "stopping the battery" in log and "stage exit codes: p5-gpu=host-gone ===" in log
    assert "step=semantics" not in log and "confirmed terminated" in log


def test_the_mac_is_kept_awake_while_a_run_lasts(net):
    (net.bin / "caffeinate").write_text('#!/bin/bash\necho "$@" >> "$FAKE_NET/caffeinate.args"\n')
    (net.bin / "caffeinate").chmod(0o755)
    env = {k: v for k, v in net.env.items() if k != "P3_NO_CAFFEINATE"}
    done = subprocess.run([BASH, "-c", LIB_PREAMBLE + "p3_keep_awake; sleep 0.5"], capture_output=True, text=True, env=env, timeout=30)
    assert "caffeinate armed" in done.stdout
    time.sleep(0.3)
    args = (net.net / "caffeinate.args").read_text().split()
    assert args[:3] == ["-i", "-s", "-w"] and args[3].isdigit()              # tied to the driver's own pid: it ends with it
    (net.net / "caffeinate.args").unlink()
    done = subprocess.run([BASH, "-c", LIB_PREAMBLE + "p3_keep_awake"], capture_output=True, text=True, env=net.env, timeout=30)     # P3_NO_CAFFEINATE set
    assert "caffeinate" not in done.stdout and not (net.net / "caffeinate.args").exists()


def fake_caffeinate(net, repo):
    """The driver's environment with a recording caffeinate on PATH and the opt-out removed."""
    (net.bin / "caffeinate").write_text('#!/bin/bash\necho "$@" >> "$FAKE_NET/caffeinate.args"\n')
    (net.bin / "caffeinate").chmod(0o755)
    return {k: v for k, v in repo.env.items() if k != "P3_NO_CAFFEINATE"}


def test_the_lambda_driver_holds_the_mac_awake_for_itself_and_for_its_watchdog(net, lambda_repo):
    (lambda_repo.probes / "lambda_watchdog.sh").write_text('#!/bin/bash\necho $$ > "$FAKE_NET/watchdog.pid"\nexec sleep 300\n')
    env = fake_caffeinate(net, lambda_repo)
    done = subprocess.run([BASH, str(lambda_repo.probes / "run_lambda_gpu_probe.sh"), "b200", "gpu_1x_b200_sxm6", "region-1", "2", "60"],
                          capture_output=True, text=True, env=env, timeout=240)
    assert done.returncode == 0, done.stdout + done.stderr
    holds = [line.split() for line in (net.net / "caffeinate.args").read_text().splitlines()]
    assert len(holds) == 2 and all(h[:3] == ["-i", "-s", "-w"] for h in holds)
    watchdog_pid = (net.net / "watchdog.pid").read_text().strip()
    assert holds[1][3] == watchdog_pid and holds[0][3] != watchdog_pid                      # one hold per process: the driver, then the watchdog
    log = published_log(lambda_repo)
    assert "caffeinate armed" in done.stdout and "for the watchdog (pid " + watchdog_pid + ")" in done.stdout
    assert "confirmed terminated" in log
    for _ in range(40):                                                                      # once the instance is confirmed gone the watchdog is ended
        if subprocess.run(["kill", "-0", watchdog_pid], capture_output=True).returncode != 0:
            break
        time.sleep(0.1)
    else:
        pytest.fail("the watchdog was still running after the termination was confirmed")


def test_the_gcp_driver_holds_the_mac_awake_for_its_watchdog_too(net, gcp_repo):
    (gcp_repo.probes / "gcp_watchdog.sh").write_text('#!/bin/bash\necho $$ > "$FAKE_NET/watchdog.pid"\nexec sleep 300\n')
    env = fake_caffeinate(net, gcp_repo)
    done = subprocess.run([BASH, str(gcp_repo.probes / "run_gcp_cpu_probe.sh"), "us-east4-a"], capture_output=True, text=True,
                          env=env, timeout=240)
    assert done.returncode == 0, done.stdout + done.stderr
    holds = [line.split() for line in (net.net / "caffeinate.args").read_text().splitlines()]
    assert len(holds) == 2 and holds[1][3] == (net.net / "watchdog.pid").read_text().strip() and holds[0][3] != holds[1][3]
