#!/usr/bin/env bash
# run_h100_smoke.sh LAMBDA_TYPE REGION HOURS [WATCHDOG_SECONDS]
#
# The pre-round-1 readiness (smoke) test of the Paper 3 harness on ONE Lambda 1x H100 (design section 7.2, "Harness readiness"),
# end to end with no dependency on an operator returning mid-run. Synthetic load only: generated Parquet and three trivial
# queries (smoke_workloads.py); no study query, no study data, nothing in the output is a result.
#
#   launch (p3-probe-h100-smoke-owner-ejiod-expires-<UTC>) -> arm the watchdog -> wait for SSH -> copy the repository ->
#   stage setup   ./setup_h100.sh as the campaign host is brought up, timed
#   stage data    the synthetic dataset
#   stage run     run_experiment.py with the flags run_campaign.py builds for round 1 of the h100 class (smoke_command.py): four
#                 engines, scope board, window 10 s, idle windows 30 s after a 60 s settle, 3 s load pulses, power captures
#   stage gates   paper3_gates.py --host h100 with paper3_hosts.json
#   stage bundle  the run directory (no Spark event logs), gates report, captures, timings and stage logs, as one tarball
#   fetch -> redact and hash (check_smoke.py publish) -> docs/paper3-evidence/smoke/h100-<STAMP>/ -> terminate, confirm it is gone ->
#   check_smoke.py check: the checklist of what must hold (checklist.json / checklist.txt) -> SHA256SUMS.txt
#
# Every stage is started DETACHED on the host and finished exactly when its exit-code file exists (p3_detach.sh / p3_stage_lib.sh, the
# probe drivers' machinery): a sleeping Mac, a dropped connection or an ssh exit 255 never stops a stage, and polling resumes by
# itself. caffeinate keeps the Mac awake while the run lasts. The one thing a sleeping Mac cannot do is terminate the instance: this
# script and its watchdog are both Mac processes (the watchdog compares a wall-clock deadline, so it fires at the first wake past it).
#
# Source of the repository on the host: P3_SMOKE_SOURCE=head (default; git archive of HEAD, refuses when HEAD lacks the smoke files:
# commit first) or worktree (tracked and untracked-not-ignored files as they are on disk; the run log says how many are uncommitted).
# Exit status: 0 when every stage exited 0 (the checklist's own verdict is in checklist.txt), 1 otherwise.
# Other knobs: P3_SMOKE_RUNS (measured trials per query, 3), P3_SMOKE_DATA_ARGS (extra generate_smoke_data.py arguments, rehearsals only),
# P3_SMOKE_EXPECT_SETTLE (settle the checklist expects before an idle window, 60; rehearsals), P3_SMOKE_PRICE (USD per hour for the cost line, 4.89 = the H100 SXM5 price of the design), P3_SMOKE_PYTHON (local interpreter for
# check_smoke.py; default the repository's .venv, else python3), P3_STAGE_TIMEOUT_<STAGE> (seconds of polling per stage).
#
# Estimate (H100 SXM5 at the design's 4.89 USD/h, billed per minute from boot): launch to SSH ready 4-6 min (3.6 min on 2026-11-12),
# repository copy 1, setup_h100.sh 6-15 (the unknown this test measures), data 0.5, run 22-26 (four sessions of ~5.5 min: 3 min of idle
# windows, ~1.5 min of query windows, engine start-up, two 3 s pulses), gates and collection 1.5, teardown 1-2 = about 40-55 min,
# 3.3-4.5 USD. Worst case, a hung stage: the watchdog's 100 min = 8.15 USD.
#
# Example (the lead launches):
#   ./run_h100_smoke.sh gpu_1x_h100_sxm5 us-south-2 2 6000
set -uo pipefail

if [ $# -lt 3 ]; then
  echo "usage: $0 LAMBDA_TYPE REGION HOURS [WATCHDOG_SECONDS]" >&2
  exit 2
fi
LAMBDA_TYPE=$1
REGION=$2
HOURS=$3
WATCHDOG_SECONDS=${4:-6000}
CLASS=h100-smoke

HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
PROBES="$HERE/../paper3_probes"
EVIDENCE_ROOT="$REPO_ROOT/docs/paper3-evidence/smoke"
STATE_DIR=${P3_STATE_DIR:-/tmp/p3probe}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EVIDENCE_DIR="$EVIDENCE_ROOT/h100-$STAMP"
WORK=${TMPDIR:-/tmp}/p3smoke-$STAMP                # raw run log, timeline and downloads: never inside the repository
RUN_LOG="$WORK/run.log"
TIMELINE="$WORK/driver-timeline.jsonl"
STAGING="$WORK/staging"
SOURCE=${P3_SMOKE_SOURCE:-head}
PRICE=${P3_SMOKE_PRICE:-4.89}
mkdir -p "$STATE_DIR" "$STAGING" "$EVIDENCE_ROOT"
: > "$RUN_LOG"; : > "$TIMELINE"

PY=${P3_SMOKE_PYTHON:-}
if [ -z "$PY" ]; then if [ -x "$REPO_ROOT/.venv/bin/python" ]; then PY="$REPO_ROOT/.venv/bin/python"; else PY=python3; fi; fi

log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$RUN_LOG"; }
p3_log() { log "$@"; }
mark() {                 # NAME: one event of the driver's own timeline (the host's stage timings are in the bundle)
  local now; now=$(date +%s)
  printf '{"name": "%s", "epoch": %d, "utc": "%s"}\n' "$1" "$now" "$(date -u -r "$now" +%FT%TZ 2>/dev/null || date -u -d "@$now" +%FT%TZ)" >> "$TIMELINE"
}
# shellcheck source=../paper3_probes/p3_stage_lib.sh
. "$PROBES/p3_stage_lib.sh"

log "=== H100 smoke test: type=$LAMBDA_TYPE region=$REGION hours=$HOURS watchdog_s=$WATCHDOG_SECONDS source=$SOURCE ==="
log "synthetic load only: generated data and three trivial queries; no study query is run. Raw log: $RUN_LOG"
p3_keep_awake

# ---- the code that goes to the host: decided and checked before anything is launched -----------------------------------------
NEEDED=(smoke_workloads.py tools/paper3_smoke/smoke_host.sh tools/paper3_smoke/smoke_command.py tools/paper3_smoke/generate_smoke_data.py
        worker.py run_experiment.py run_campaign.py paper3_gates.py paper3_hosts.json gpu_pulse.py setup_h100.sh)
HEAD_SHA=$(git -C "$REPO_ROOT" rev-parse HEAD)
DIRTY=$(git -C "$REPO_ROOT" status --porcelain | wc -l | tr -d ' ')
ARCHIVE="$STAGING/repo.tar.gz"
case "$SOURCE" in
  head)
    for f in "${NEEDED[@]}"; do
      git -C "$REPO_ROOT" cat-file -e "HEAD:$f" 2>/dev/null || { log "HEAD $HEAD_SHA lacks $f: commit the smoke files first, or run with P3_SMOKE_SOURCE=worktree"; exit 1; }
    done
    git -C "$REPO_ROOT" archive --format=tar.gz -o "$ARCHIVE" HEAD ;;
  worktree)
    for f in "${NEEDED[@]}"; do [ -f "$REPO_ROOT/$f" ] || { log "$f is missing from the working tree"; exit 1; }; done
    ( cd "$REPO_ROOT" && git ls-files -z -co --exclude-standard | tar --null -T - -czf "$ARCHIVE" ) ;;
  *) log "P3_SMOKE_SOURCE must be head or worktree"; exit 2 ;;
esac
ARCHIVE_BYTES=$(wc -c < "$ARCHIVE" | tr -d ' ')
log "repository for the host: $SOURCE of $HEAD_SHA ($DIRTY uncommitted path(s) in the working tree), archive $ARCHIVE_BYTES bytes"
if [ "$ARCHIVE_BYTES" -gt 300000000 ]; then log "archive over 300 MB: something large is tracked; aborting"; exit 1; fi

# ---- launch ------------------------------------------------------------------------------------------------------------------
mark create_call
LAUNCH_OUT=$("$PROBES/lambda.sh" launch "$CLASS" "$LAMBDA_TYPE" "$REGION" "$HOURS" 2>>"$RUN_LOG")
LAUNCH_RC=$?
ID=$(printf '%s\n' "$LAUNCH_OUT" | head -1 | tr -d '[:space:]')
log "launch exit=$LAUNCH_RC id=${ID:-<none>}"
if [ $LAUNCH_RC -ne 0 ] || [ -z "$ID" ]; then log "LAUNCH FAILED; nothing to terminate"; exit 1; fi
mark launched
NAME=$(cat "$STATE_DIR/$ID.name" 2>/dev/null || echo unknown)
log "instance name: $NAME"

nohup "$PROBES/lambda_watchdog.sh" "$ID" "$WATCHDOG_SECONDS" >>"$WORK/watchdog.log" 2>&1 &
WATCHDOG_PID=$!
disown
log "watchdog armed pid=$WATCHDOG_PID deadline_s=$WATCHDOG_SECONDS (wall-clock deadline; backstop, this script terminates the instance itself)"
p3_keep_awake "$WATCHDOG_PID" "the watchdog"

IP=""
TERMINATED=0
terminate_and_confirm() {
  [ "$TERMINATED" = 1 ] && return 0
  TERMINATED=1
  mark terminate_call
  log "terminating $ID"
  "$PROBES/lambda.sh" terminate "$ID" >>"$RUN_LOG" 2>&1
  local deadline=$(( $(date +%s) + 600 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local listing list_rc
    listing=$("$PROBES/lambda.sh" list 2>>"$RUN_LOG"); list_rc=$?
    if [ $list_rc -eq 0 ] && ! printf '%s\n' "$listing" | cut -f1 | grep -qx "$ID"; then
      mark terminated
      log "confirmed terminated: $ID no longer listed"
      kill "$WATCHDOG_PID" 2>/dev/null
      return 0
    fi
    sleep 15
  done
  log "WARNING: could not confirm termination of $ID within 10 minutes; manual check required"
  return 1
}
finalize() {             # after teardown: the timeline and the redacted driver log join the evidence, then the checklist and the hashes
  [ -d "$EVIDENCE_DIR" ] || return 0
  mark finalize
  cp "$TIMELINE" "$EVIDENCE_DIR/driver-timeline.jsonl"
  grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+Z ' "$RUN_LOG" | sed -E 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<ip>/g' > "$EVIDENCE_DIR/driver.log"
  "$PY" "$HERE/check_smoke.py" check "$EVIDENCE_DIR" --price-per-hour "$PRICE" --expect-idle-settle-s "${P3_SMOKE_EXPECT_SETTLE:-60}" >>"$RUN_LOG" 2>&1
  log "checklist exit=$? (see $EVIDENCE_DIR/checklist.txt)"
  "$PY" "$HERE/check_smoke.py" sums "$EVIDENCE_DIR" >>"$RUN_LOG" 2>&1
  [ -f "$EVIDENCE_DIR/checklist.txt" ] && cat "$EVIDENCE_DIR/checklist.txt" | tee -a "$RUN_LOG"
}
on_exit() { terminate_and_confirm; finalize; }
trap on_exit EXIT
trap 'log "interrupted (signal); tearing the instance down"; exit 130' INT
trap 'log "terminated (signal); tearing the instance down"; exit 143' TERM HUP

"$PROBES/lambda.sh" wait "$ID" >>"$RUN_LOG" 2>&1
WAIT_RC=$?
log "ssh wait exit=$WAIT_RC"
[ $WAIT_RC -eq 0 ] || { log "SSH never became ready; aborting (teardown runs via the exit trap)"; exit 1; }
mark ssh_ready
IP=$(cat "$STATE_DIR/$ID.ip")
SSHOPTS=(-i "$HOME/.ssh/lambda_key22" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
P3_SSH=(ssh "${SSHOPTS[@]}" "ubuntu@$IP")
P3_SCP_FROM=(scp "${SSHOPTS[@]}")
P3_SCP_TO=(scp "${SSHOPTS[@]}")
P3_REMOTE_PREFIX="ubuntu@$IP:"
p3_host_alive() {
  local listing
  listing=$("$PROBES/lambda.sh" list 2>>"$RUN_LOG") || return 0
  printf '%s\n' "$listing" | cut -f1 | grep -qx "$ID"
}

P3_SCP_BOUND=1200 p3_copy_to "$ARCHIVE" "$P3_REMOTE_PREFIX~/repo.tar.gz" || { log "copying the repository failed; aborting"; exit 1; }
p3_copy_to "$PROBES/p3_detach.sh" "$P3_REMOTE_PREFIX~/p3_detach.sh" || { log "copying p3_detach.sh failed; aborting"; exit 1; }
p3_ssh_retry 'rm -rf ~/gpu-spark-polars-study && mkdir -p ~/gpu-spark-polars-study ~/p3out && tar -xzf ~/repo.tar.gz -C ~/gpu-spark-polars-study' >/dev/null \
  || { log "unpacking the repository failed; aborting"; exit 1; }
HOST_NAME=$(p3_ssh_retry 'hostname' 2>/dev/null | tail -n 1 | tr -d '[:space:]')
mark repo_copied
log "repository unpacked on the host"

STAGE_RESULTS=""
OVERALL=0
run_stage() {            # STAGE BUDGET_SECONDS COMMAND [ARG...]: detached start, wait for the exit-code file; STEP_RC = code or failure state
  local stage=$1 budget=$2
  shift 2
  STEP_RC=""; P3_STAGE_STATE=""
  mark "${stage}_start"
  if ! p3_stage_start "$stage" "$@"; then STEP_RC=start-failed; mark "${stage}_end"; STAGE_RESULTS="$STAGE_RESULTS $stage=$STEP_RC"; return 1; fi
  P3_STAGE_TIMEOUT=$budget
  if p3_stage_wait "$stage"; then STEP_RC=$P3_STAGE_RC; else STEP_RC=$P3_STAGE_STATE; fi
  mark "${stage}_end"
  STAGE_RESULTS="$STAGE_RESULTS $stage=$STEP_RC"
  log "stage $stage exit=$STEP_RC"
  [ "$STEP_RC" = 0 ] || OVERALL=1
  [ "$STEP_RC" = 0 ]
}
HOST_SCRIPT=gpu-spark-polars-study/tools/paper3_smoke/smoke_host.sh

setup_ok=0 data_ok=0
if run_stage setup "${P3_STAGE_TIMEOUT_SETUP:-2700}" bash "$HOST_SCRIPT" setup; then
  setup_ok=1
  run_stage data "${P3_STAGE_TIMEOUT_DATA:-900}" env "P3_SMOKE_DATA_ARGS=${P3_SMOKE_DATA_ARGS:-}" bash "$HOST_SCRIPT" data && data_ok=1
fi
if [ $setup_ok = 1 ] && [ $data_ok = 1 ]; then
  run_stage run "${P3_STAGE_TIMEOUT_RUN:-3600}" env "P3_SMOKE_RUNS=${P3_SMOKE_RUNS:-3}" bash "$HOST_SCRIPT" run
  # the gates read whatever the run left behind (an engine failure is a finding, not a reason to skip them); not after a lost host
  if [ "$P3_STAGE_STATE" != host-gone ]; then run_stage gates "${P3_STAGE_TIMEOUT_GATES:-600}" bash "$HOST_SCRIPT" gates; fi
else
  log "setup or data stage failed: the run and the gates are skipped; the logs are still collected"
fi

# ---- collect, redact, hash ---------------------------------------------------------------------------------------------------
if [ "$P3_STAGE_STATE" != host-gone ] && run_stage bundle "${P3_STAGE_TIMEOUT_BUNDLE:-600}" bash "$HOST_SCRIPT" bundle; then
  if p3_fetch "p3out/smoke-evidence.tar.gz" "$STAGING/"; then
    mkdir -p "$STAGING/bundle" && tar -xzf "$STAGING/smoke-evidence.tar.gz" -C "$STAGING/bundle"
    mark fetched
    "$PY" "$HERE/check_smoke.py" publish "$STAGING/bundle" "$EVIDENCE_DIR" --mask "$IP" --mask "$HOST_NAME" --mask "$NAME" >>"$RUN_LOG" 2>&1
    log "publish exit=$? into $EVIDENCE_DIR"
  else
    log "WARNING: could not fetch the evidence bundle"
  fi
fi
log "=== stages:$STAGE_RESULTS; teardown and the checklist follow via the exit trap ==="
exit "$OVERALL"
