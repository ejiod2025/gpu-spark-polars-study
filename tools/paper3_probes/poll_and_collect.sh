#!/usr/bin/env bash
# poll_and_collect.sh CLASS ID IP STAMP END_EPOCH
#
# Companion to battery_runner.sh: that script runs the semantics+p6+g8 battery detached on the remote host
# (no long-lived SSH session), and this one polls for its progress with short, strictly bounded SSH calls
# every ~2 minutes (never a call that can itself hang for the length of a stage), collecting each stage's
# output into docs/paper3-evidence/probes/ as soon as it appears, with sha256 + non-empty verification.
# When p3out/DONE appears (or END_EPOCH, a Unix timestamp, is reached first) it does one final sweep, then
# terminates the instance and polls /instances until it is gone or 10 minutes pass. CLASS is used only for
# evidence file naming; ID is the Lambda instance id (for lambda.sh terminate/list); IP is used for ssh/scp
# directly (not re-resolved from lambda.sh's state files, so this still works if they are stale).
#
# Bounded commands: every ssh/scp call below runs under `bounded`, a short race between the real command
# and a sleep+kill watchdog (there is no `timeout`(1) on macOS), so a dead connection surfaces as a failure
# within BOUND_SECONDS instead of hanging the poll loop the way the original long-lived battery SSH did.
set -uo pipefail

CLASS=$1; ID=$2; IP=$3; STAMP=$4; END_EPOCH=$5
HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
EVIDENCE_DIR="$REPO_ROOT/docs/paper3-evidence/probes"
mkdir -p "$EVIDENCE_DIR"
RUN_LOG="$HERE/poll-${CLASS}-${STAMP}.log"
SHAFILE="$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-SHA256SUMS.txt"
STAGING="$HERE/.staging-poll-${CLASS}-${STAMP}"
mkdir -p "$STAGING"
: > "$RUN_LOG"
BOUND_SECONDS=25
POLL_INTERVAL=120

SSHOPTS=(-i "$HOME/.ssh/lambda_key22" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
         -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=10 -o ServerAliveCountMax=2)

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$RUN_LOG"; }

bounded() {              # bounded CMD... : run CMD with a hard wall-clock cap of BOUND_SECONDS
  "$@" &
  local cmd_pid=$!
  ( sleep "$BOUND_SECONDS"; kill -9 "$cmd_pid" 2>/dev/null ) &
  local guard_pid=$!
  wait "$cmd_pid" 2>/dev/null
  local rc=$?
  kill "$guard_pid" 2>/dev/null
  wait "$guard_pid" 2>/dev/null
  return $rc
}

remote_ls() {
  bounded ssh "${SSHOPTS[@]}" "ubuntu@$IP" 'ls ~/p3out/ 2>/dev/null'
}

collect_set() {          # collect_set STEP FILE...   (copies into STAGING, then into the evidence dir)
  local step=$1; shift
  local f
  for f in "$@"; do
    bounded scp "${SSHOPTS[@]}" "ubuntu@$IP:~/p3out/$f" "$STAGING/" >>"$RUN_LOG" 2>&1
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
      log "WARNING: [$step] $base missing or empty after scp (not collected this pass)"
    fi
    rm -f "$src"
  done
  shopt -u nullglob
}

terminate_and_confirm() {
  log "terminating $ID"
  "$HERE/lambda.sh" terminate "$ID" >>"$RUN_LOG" 2>&1
  local deadline=$(( $(date +%s) + 600 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local listing
    listing=$("$HERE/lambda.sh" list 2>>"$RUN_LOG")
    if [ $? -eq 0 ] && ! printf '%s\n' "$listing" | cut -f1 | grep -qx "$ID"; then
      log "confirmed terminated: $ID no longer listed"
      rm -rf "$STAGING"
      return 0
    fi
    sleep 15
  done
  log "WARNING: could not confirm termination of $ID within 10 minutes; manual check required"
  rm -rf "$STAGING"
  return 1
}

log "=== polling class=$CLASS id=$ID stamp=$STAMP, hard end $(date -u -r "$END_EPOCH" +%FT%TZ 2>/dev/null || date -u -d "@$END_EPOCH" +%FT%TZ) ==="

got_semantics=0 got_p6=0 got_g8=0 got_done=0
while [ "$(date +%s)" -lt "$END_EPOCH" ]; do
  listing=$(remote_ls)
  log "remote p3out/ listing: $(printf '%s' "$listing" | tr '\n' ' ')"

  if [ $got_semantics -eq 0 ] && printf '%s\n' "$listing" | grep -qx "semantics.json"; then
    collect_set semantics "semantics.json" "semantics-50ms.csv" "semantics-scope-20ms.csv"
    got_semantics=1
  fi
  if [ $got_p6 -eq 0 ] && printf '%s\n' "$listing" | grep -qx "p6.json"; then
    collect_set p6 "p6.json" "p6-power-20ms.csv"
    got_p6=1
  fi
  if [ $got_g8 -eq 0 ] && printf '%s\n' "$listing" | grep -qx "g8.json"; then
    collect_set g8 "g8.json" "g8-gpu.csv"
    got_g8=1
  fi
  # battery.log changes throughout the run; re-fetch every pass so we always have the latest exit codes
  bounded scp "${SSHOPTS[@]}" "ubuntu@$IP:~/p3out/battery.log" "$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-battery.log" >>"$RUN_LOG" 2>&1

  if printf '%s\n' "$listing" | grep -qx "DONE"; then
    got_done=1
    log "DONE marker present: battery finished on-host"
    break
  fi
  sleep "$POLL_INTERVAL"
done

if [ $got_done -eq 0 ]; then
  log "hard end reached ($(date -u +%FT%TZ)) without a DONE marker; doing a final best-effort sweep anyway"
fi

# Final sweep: whatever stages were not individually collected above (e.g. several finished between polls,
# or the run never reached DONE), try once more; also always re-pull battery.log one last time.
[ $got_semantics -eq 0 ] && collect_set semantics "semantics.json" "semantics-50ms.csv" "semantics-scope-20ms.csv"
[ $got_p6 -eq 0 ] && collect_set p6 "p6.json" "p6-power-20ms.csv"
[ $got_g8 -eq 0 ] && collect_set g8 "g8.json" "g8-gpu.csv"
bounded scp "${SSHOPTS[@]}" "ubuntu@$IP:~/p3out/battery.log" "$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-battery.log" >>"$RUN_LOG" 2>&1
bounded scp "${SSHOPTS[@]}" "ubuntu@$IP:~/p3out/battery_launch.log" "$EVIDENCE_DIR/lambda-${CLASS}-${STAMP}-battery_launch.log" >>"$RUN_LOG" 2>&1

log "=== collection state: semantics=$got_semantics p6=$got_p6 g8=$got_g8 done_marker=$got_done ==="
terminate_and_confirm
log "=== poll_and_collect finished for $CLASS ==="
