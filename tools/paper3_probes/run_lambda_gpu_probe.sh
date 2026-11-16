#!/usr/bin/env bash
# run_lambda_gpu_probe.sh CLASS LAMBDA_TYPE REGION HOURS [WATCHDOG_SECONDS]
#
# Drives one Lambda GPU probe host end-to-end with no dependency on an operator returning mid-run: launch -> arm the
# watchdog -> wait for SSH -> copy the probe scripts -> a 2-pulse smoke test (canary only, not a result: skips the
# paid battery if the untested CUDA path does not load) -> p5-gpu -> semantics -> p6 -> g8 -> sources -> after EVERY stage,
# copy that stage's outputs into docs/paper3-evidence/probes/ (so a late failure still leaves earlier stages on disk),
# verify each copied file is non-empty and record its sha256 -> terminate -> poll /instances until the instance is
# gone or 10 minutes pass -> write one local log with every stage's exit code.
#
# No stage depends on one live SSH session (the 2026-11-16 B200 battery lost p5-gpu, p6, g8 and sources to ssh exit 255
# when the driving Mac slept for hours): every stage, the smoke test included, is started DETACHED on the host
# (p3_detach.sh START: own session, all descriptors redirected, the start call returns at once), writes its output to
# ~/p3out/<stage>.log and its exit code, last, to ~/p3out/<stage>.rc, and is finished exactly when that file exists. The driver
# polls for it with short time-bounded ssh calls (p3_stage_lib.sh); a failed poll is retried, never fatal, and polling resumes
# after the Mac wakes; files are fetched with bounded, retried scp. caffeinate keeps the Mac awake while the run lasts.
# What a sleeping Mac cannot do: terminate the instance. This script and its watchdog are both Mac processes, so nothing
# ends the instance until the Mac wakes (the watchdog's sleep does not count the time asleep); the run log says how long each
# poll gap was ("... s since the previous poll").
#
# Synthetic load only (the CUDA spin kernel and nvidia-smi pollers in p3_gpu_probes.py); no study query is ever run.
# The instance is always named p3-probe-<CLASS>-owner-ejiod-expires-<UTC> (lambda.sh's convention) and only ever
# touched by its own id. Intended to be run with Bash run_in_background so completion (success or failure) arrives
# as a single notification; nothing it does depends on anyone watching it run.
#
# Example: ./run_lambda_gpu_probe.sh h100 gpu_1x_h100_sxm5 us-south-2 1 4500
# P3_STEPS selects stages, P3_PROBE_ARGS is appended to every p3_gpu_probes.py stage (e.g. a p6-only GH200 rerun:
# P3_STEPS=p6 P3_PROBE_ARGS="--p6-field module.power.draw.instant"); P3_STAGE_TIMEOUT, P3_POLL_SECONDS ... : p3_stage_lib.sh.
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
p3_log() { log "$@"; }
# shellcheck source=p3_stage_lib.sh
. "$HERE/p3_stage_lib.sh"

log "=== starting probe run: class=$CLASS type=$LAMBDA_TYPE region=$REGION hours=$HOURS watchdog_s=$WATCHDOG_SECONDS ==="
log "no study queries will be run (synthetic CUDA spin kernel and nvidia-smi pollers only)"
p3_keep_awake

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
log "watchdog armed pid=$WATCHDOG_PID deadline_s=$WATCHDOG_SECONDS (wall-clock deadline; backstop only, this script terminates the instance itself once done)"
p3_keep_awake "$WATCHDOG_PID" "the watchdog"

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
      kill "$WATCHDOG_PID" 2>/dev/null       # nothing left to guard; ends the watchdog's caffeinate hold with it
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
SSHOPTS=(-i "$HOME/.ssh/lambda_key22" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
# Keepalives and p3_bounded's hard time limit on every call: a connection dropped by a NAT, a laptop that slept or a
# half-open TCP session ends as a failed call that the polling loop retries, never as an ssh that hangs.
P3_SSH=(ssh "${SSHOPTS[@]}" "ubuntu@$IP")
P3_SCP_FROM=(scp "${SSHOPTS[@]}")
P3_SCP_TO=(scp "${SSHOPTS[@]}")
P3_REMOTE_PREFIX="ubuntu@$IP:"
p3_host_alive() {        # 0 unless the API lists our instances and ours is not among them (an API failure is "unknown": alive)
  local listing
  listing=$("$HERE/lambda.sh" list 2>>"$RUN_LOG") || return 0
  printf '%s\n' "$listing" | cut -f1 | grep -qx "$ID"
}

if ! p3_copy_to "$REPO_ROOT/power_telemetry.py" "$REPO_ROOT/probe_power.py" "$HERE/p3_gpu_probes.py" "$HERE/p3_detach.sh" "ubuntu@$IP:~/"; then
  log "initial scp of scripts failed after $P3_RETRIES attempts; aborting run (teardown runs via the exit trap)"
  exit 1
fi
log "initial scp of scripts exit=0"
if ! p3_ssh_retry 'mkdir -p ~/p3out ~/p3smoke' >/dev/null; then
  log "could not create the output directories on the host; aborting run (teardown runs via the exit trap)"
  exit 1
fi

LOGS="$STAGING/.logs"
mkdir -p "$LOGS"
stage_log_to_run_log() {   # STEP: the stage's own output lives on the host; keep its tail in the raw run log (never published)
  if p3_fetch "p3out/$1.log" "$LOGS/"; then
    { echo "----- tail of the host's $1.log -----"; tail -n 40 "$LOGS/$1.log"; echo "----- end -----"; } >> "$RUN_LOG"
    rm -f "$LOGS/$1.log"
  else
    echo "(could not fetch the host's $1.log)" >> "$RUN_LOG"
  fi
}

run_stage() {            # STAGE COMMAND [ARG...]: detached start, wait for the exit-code file; sets STEP_RC to the code or the failure state
  local stage=$1
  STEP_RC=""; P3_STAGE_STATE=""
  if ! p3_stage_start "$@"; then
    STEP_RC=start-failed
    return 1
  fi
  if p3_stage_wait "$stage"; then STEP_RC=$P3_STAGE_RC; else STEP_RC=$P3_STAGE_STATE; fi
  [ "$P3_STAGE_STATE" = host-gone ] || stage_log_to_run_log "$stage"
  [ "$STEP_RC" = 0 ]
}

run_stage smoke python3 p3_gpu_probes.py g8 --out p3smoke --g8-pulses 2
SMOKE_RC=$STEP_RC
log "pre-flight smoke test (2 pulses, canary only, NOT a result) exit=$SMOKE_RC"
if [ "$SMOKE_RC" != 0 ]; then
  log "smoke test failed; skipping the paid battery and going straight to teardown"
  exit 1
fi
p3_ssh_retry 'rm -rf ~/p3smoke' >/dev/null || true

collect_stage() {        # STEP  REMOTE_GLOB... (relative to ~/p3out)
  local step=$1; shift
  local names name src dst sha size
  if ! names=$(p3_remote_names "$@"); then
    log "WARNING: [$step] could not list the host's files (ssh kept failing); nothing collected"
    return 1
  fi
  if [ -z "$names" ]; then
    log "WARNING: [$step] no file on the host matches: $*"
    return 0
  fi
  while IFS= read -r name; do
    src="$STAGING/$name"
    dst="$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-${name}"
    if p3_fetch "p3out/$name" "$STAGING/" && [ -s "$src" ]; then
      cp "$src" "$dst"
      sha=$(shasum -a 256 "$dst" | awk '{print $1}')
      size=$(wc -c < "$dst" | tr -d ' ')
      echo "$sha  $(basename "$dst")  ${size}B" >> "$SHAFILE"
      log "collected [$step] $(basename "$dst") sha256=$sha size=${size}B"
    else
      log "WARNING: [$step] $name is missing or empty after scp (not collected)"
    fi
    rm -f "$src"
  done <<< "$names"
}

run_step() {             # STEP
  local step=$1
  local -a extra=()
  if [ "$step" = sources ]; then   # P7 CPU-energy source check: powercap, hwmon, perf, IPMI, virtualisation (read-only)
    run_stage "$step" python3 probe_power.py --lease-name "$NAME" --no-load --output p3out/sources.json
  else
    read -r -a extra <<< "$PROBE_ARGS"
    run_stage "$step" python3 p3_gpu_probes.py "$step" --out p3out ${extra[@]+"${extra[@]}"}
  fi
  log "step=$step exit=$STEP_RC"
}

# P3_STEPS selects stages (default: the full battery); P3_PROBE_ARGS is appended to every p3_gpu_probes.py stage, e.g. a
# p6-only GH200 rerun: P3_STEPS=p6 P3_PROBE_ARGS="--p6-field module.power.draw.instant".
STEPS_TO_RUN=${P3_STEPS:-p5-gpu semantics p6 g8 sources}
PROBE_ARGS=${P3_PROBE_ARGS:-}
log "stages: [$STEPS_TO_RUN] extra args: [${PROBE_ARGS}]"
SUMMARY=""
for step in $STEPS_TO_RUN; do
  run_step "$step"
  rc=$STEP_RC
  if [ "$P3_STAGE_STATE" = host-gone ]; then
    SUMMARY="$SUMMARY $step=$rc"
    log "the provider no longer lists the instance: stopping the battery (teardown runs via the exit trap)"
    break
  fi
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
