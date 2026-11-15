#!/usr/bin/env bash
# run_lambda_gpu_probe.sh CLASS LAMBDA_TYPE REGION HOURS [WATCHDOG_SECONDS]
#
# Drives one Lambda GPU probe host end-to-end with no dependency on an operator returning mid-run: launch -> arm the
# watchdog -> wait for SSH -> copy the probe scripts -> a 2-pulse smoke test (canary only, not a result: skips the
# paid battery if the untested CUDA path does not load) -> p5-gpu -> semantics -> p6 -> g8 -> after EVERY stage, copy
# that stage's outputs into docs/paper3-evidence/probes/ (so a late failure still leaves earlier stages on disk),
# verify each copied file is non-empty and record its sha256 -> terminate -> poll /instances until the instance is
# gone or 10 minutes pass -> write one local log with every stage's exit code.
#
# Synthetic load only (the CUDA spin kernel and nvidia-smi pollers in p3_gpu_probes.py); no study query is ever run.
# The instance is always named p3-probe-<CLASS>-owner-ejiod-expires-<UTC> (lambda.sh's convention) and only ever
# touched by its own id. Intended to be run with Bash run_in_background so completion (success or failure) arrives
# as a single notification; nothing it does depends on anyone watching it run.
#
# Example: ./run_lambda_gpu_probe.sh h100 gpu_1x_h100_sxm5 us-south-2 1 4500
set -uo pipefail

if [ $# -lt 4 ]; then
  echo "usage: $0 CLASS LAMBDA_TYPE REGION HOURS [WATCHDOG_SECONDS]" >&2
  exit 2
fi
CLASS=$1
LAMBDA_TYPE=$2
REGION=$3
HOURS=$4
WATCHDOG_SECONDS=${5:-3300}

HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
EVIDENCE_DIR="$REPO_ROOT/docs/paper3-evidence/probes"
STATE_DIR=/tmp/p3probe
mkdir -p "$STATE_DIR" "$EVIDENCE_DIR"

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
RUN_LOG="$HERE/run-${CLASS}-${STAMP}.log"
SHAFILE="$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-SHA256SUMS.txt"
STAGING="$HERE/.staging-${CLASS}-${STAMP}"
mkdir -p "$STAGING"
: > "$RUN_LOG"

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$RUN_LOG"; }

log "=== starting probe run: class=$CLASS type=$LAMBDA_TYPE region=$REGION hours=$HOURS watchdog_s=$WATCHDOG_SECONDS ==="
log "no study queries will be run (synthetic CUDA spin kernel and nvidia-smi pollers only)"

LAUNCH_OUT=$("$HERE/lambda.sh" launch "$CLASS" "$LAMBDA_TYPE" "$REGION" "$HOURS" 2>>"$RUN_LOG")
LAUNCH_RC=$?
ID=$(printf '%s\n' "$LAUNCH_OUT" | head -1 | tr -d '[:space:]')
log "launch exit=$LAUNCH_RC id=${ID:-<none>}"
if [ $LAUNCH_RC -ne 0 ] || [ -z "$ID" ]; then
  log "LAUNCH FAILED; aborting (nothing to terminate)"
  exit 1
fi
NAME=$(cat "$STATE_DIR/$ID.name" 2>/dev/null || echo "unknown")
log "instance name: $NAME"

nohup "$HERE/lambda_watchdog.sh" "$ID" "$WATCHDOG_SECONDS" >>"$HERE/watchdog-${CLASS}-${STAMP}.log" 2>&1 &
WATCHDOG_PID=$!
disown
log "watchdog armed pid=$WATCHDOG_PID deadline_s=$WATCHDOG_SECONDS (backstop only; this script terminates the instance itself once done)"

TERMINATED=0
terminate_and_confirm() {
  if [ "$TERMINATED" = "1" ]; then
    return 0
  fi
  TERMINATED=1
  log "terminating $ID"
  "$HERE/lambda.sh" terminate "$ID" >>"$RUN_LOG" 2>&1
  local deadline=$(( $(date +%s) + 600 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local listing
    listing=$("$HERE/lambda.sh" list 2>>"$RUN_LOG")
    local list_rc=$?
    if [ $list_rc -eq 0 ] && ! printf '%s\n' "$listing" | cut -f1 | grep -qx "$ID"; then
      log "confirmed terminated: $ID no longer listed (took <= 10 min)"
      rm -rf "$STAGING"
      return 0
    fi
    sleep 15
  done
  log "WARNING: could not confirm termination of $ID within 10 minutes; manual check required"
  rm -rf "$STAGING"
  return 1
}
publish_run_log() {      # redacted copy of this run's log into the evidence dir: only the driver's own timestamped
  # lines (stages, exit codes, collection, termination); raw API responses (instance IP, tokens) are dropped
  grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+Z ' "$RUN_LOG" | sed -E 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<ip>/g' \
    > "$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-run.log"
}
on_exit() { terminate_and_confirm; publish_run_log; }
trap on_exit EXIT

"$HERE/lambda.sh" wait "$ID" >>"$RUN_LOG" 2>&1
WAIT_RC=$?
log "ssh wait exit=$WAIT_RC"
if [ $WAIT_RC -ne 0 ]; then
  log "SSH never became ready; aborting run (teardown runs via the exit trap)"
  exit 1
fi
IP=$(cat "$STATE_DIR/$ID.ip")
SSHOPTS=(-i "$HOME/.ssh/lambda_key22" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=4)
# ServerAliveInterval/CountMax: a probe step like p5-gpu sends nothing over the SSH channel for ~10 minutes (the
# nvidia-smi pollers write to local files on the remote host, not to stdout), so the connection is idle the whole
# time; without keepalives a NAT/firewall can silently drop it and ssh then hangs forever waiting for a close that
# already happened invisibly (observed: a p5-gpu step finished on the host, confirmed by p5-gpu.json's mtime and no
# running python3 process, while the local ssh call that ran it stayed blocked for 28+ minutes with nothing to show
# for it). Keepalives every 15s, give up after 4 missed (60s), so a truly dead connection is detected and surfaced as
# a failed step instead of hanging the whole run.

remote_ssh() { ssh "${SSHOPTS[@]}" "ubuntu@$IP" "$@"; }

scp "${SSHOPTS[@]}" "$REPO_ROOT/power_telemetry.py" "$REPO_ROOT/probe_power.py" "$HERE/p3_gpu_probes.py" "ubuntu@$IP:~/" >>"$RUN_LOG" 2>&1
SCP_RC=$?
log "initial scp of scripts exit=$SCP_RC"
if [ $SCP_RC -ne 0 ]; then
  log "could not copy scripts to host; aborting run (teardown runs via the exit trap)"
  exit 1
fi
remote_ssh 'mkdir -p ~/p3out ~/p3smoke' >>"$RUN_LOG" 2>&1

remote_ssh 'cd ~ && python3 p3_gpu_probes.py g8 --out p3smoke --g8-pulses 2' >>"$RUN_LOG" 2>&1
SMOKE_RC=$?
log "pre-flight smoke test (2 pulses, canary only, NOT a result) exit=$SMOKE_RC"
if [ $SMOKE_RC -ne 0 ]; then
  log "smoke test failed; skipping the paid battery and going straight to teardown"
  exit 1
fi
remote_ssh 'rm -rf ~/p3smoke' >>"$RUN_LOG" 2>&1

collect_stage() {        # STEP  REMOTE_GLOB...
  local step=$1; shift
  local pattern
  for pattern in "$@"; do
    scp "${SSHOPTS[@]}" "ubuntu@$IP:~/p3out/$pattern" "$STAGING/" >>"$RUN_LOG" 2>&1
  done
  shopt -s nullglob
  local src base dst sha size
  for src in "$STAGING"/*; do
    base=$(basename "$src")
    dst="$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-${base}"
    if [ -s "$src" ]; then
      cp "$src" "$dst"
      sha=$(shasum -a 256 "$dst" | awk '{print $1}')
      size=$(wc -c < "$dst" | tr -d ' ')
      echo "$sha  $(basename "$dst")  ${size}B" >> "$SHAFILE"
      log "collected [$step] $(basename "$dst") sha256=$sha size=${size}B"
    else
      log "WARNING: [$step] $base is missing or empty after scp (not collected)"
    fi
    rm -f "$src"
  done
  shopt -u nullglob
}

run_step() {             # STEP
  local step=$1
  if [ "$step" = sources ]; then   # P7 CPU-energy source check: powercap, hwmon, perf, IPMI, virtualisation (read-only)
    remote_ssh "cd ~ && python3 probe_power.py --lease-name '$NAME' --no-load --output p3out/sources.json > /dev/null" >>"$RUN_LOG" 2>&1
  else
    remote_ssh "cd ~ && python3 p3_gpu_probes.py $step --out p3out $PROBE_ARGS" >>"$RUN_LOG" 2>&1
  fi
  local rc=$?
  log "step=$step exit=$rc"
  return $rc
}

# P3_STEPS selects stages (default: the full battery); P3_PROBE_ARGS is appended to every stage, e.g. a p6-only
# GH200 rerun: P3_STEPS=p6 P3_PROBE_ARGS="--p6-field module.power.draw.instant".
STEPS_TO_RUN=${P3_STEPS:-p5-gpu semantics p6 g8 sources}
PROBE_ARGS=${P3_PROBE_ARGS:-}
log "stages: [$STEPS_TO_RUN] extra args: [${PROBE_ARGS}]"
SUMMARY=""
for step in $STEPS_TO_RUN; do
  run_step "$step"; rc=$?
  case $step in
    p5-gpu)    collect_stage p5-gpu "p5-gpu.json" "p5-gpu-*.csv" ;;
    semantics) collect_stage semantics "semantics.json" "semantics-50ms.csv" "semantics-scope-20ms.csv" ;;
    p6)        collect_stage p6 "p6.json" "p6-power-20ms.csv" ;;
    g8)        collect_stage g8 "g8.json" "g8-gpu.csv" ;;
    sources)   collect_stage sources "sources.json" ;;
    *)         log "unknown stage $step (nothing collected)" ;;
  esac
  SUMMARY="$SUMMARY $step=$rc"
done

log "=== stage exit codes:$SUMMARY ==="
log "=== probe stages complete; teardown runs next via the exit trap ==="
exit 0
