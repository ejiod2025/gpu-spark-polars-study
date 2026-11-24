#!/usr/bin/env bash
# p3_driver.sh RECORD.json [--dest DIR] [--config-commit SHA] [--on-stop terminate|hold] [--no-teardown] [--max-resume N] [--fresh] [--resume-bringup] [--stop-after-bringup]
#
# Drives ONE launched campaign instance (the record p3_launch.sh wrote) from bring-up to teardown with no dependence on an operator
# or on one long SSH session. Start it with Bash run_in_background: completion, success or failure, arrives as one notification.
#
#   bring-up          p3_plan.py's generated script, detached on the host: checkout of the campaign commit, lease file, setup_h100.sh,
#                     inputs (generate and verify), SF1 smoke preflight; on C-GCP also the pin check and the G12 captures
#   review (C-GCP)    exit 10 after bring-up until --config-commit SHA names the commit that holds the reviewed allow-list
#   each round K      one detached run_campaign.py invocation with --stop-after-round K (the last round of the launch runs to the end),
#                     polled with short bounded SSH calls; then, while NOTHING runs on the host, pack + copy + verify the round
#                     (p3_host_pack.sh; sha256 of the archive and of every file), then the QA view of what arrived
#   end               an inventory check (every file of the campaign root on the host is on this machine), then teardown and confirmation
#
# Why one invocation per round and a pause between: copying while a round runs would put an scp/ssh load and disk reads on the host
# during timed sessions (design 3.2 controls: no other workload), and Paper 1 copied after every round. run_campaign.py is resumable
# and idempotent, so a pause costs minutes, not data (docs/PAPER3_RUNBOOK.md has the cost).
#
# Sleep-proof like the probe drivers (tools/paper3_probes/p3_stage_lib.sh): every long step is a detached stage finished exactly when
# its exit-code file exists; a failed poll is retried, never fatal; caffeinate holds the Mac awake; the lease watchdog (armed by
# p3_launch.sh) is a wall-clock deadline. Restartable: stage names are fixed per round and attempt, a started stage is attached to, a
# verified pack leaves a marker in DEST/.verified, so running the driver again continues where it was.
#
# --fresh: after a hold whose cause a person has fixed (a bring-up step, a refused lease or host config), start the stage again under its next
# attempt name instead of attaching to the finished failure. Without it a re-run of the driver attaches to what is on the host, so it never
# starts a second copy of a stage and never repeats a failure by accident.
#
# Blinding (design 7.3): a log line of the bring-up or of a round can carry a wall time (the preflight prints `[PASS] run.baseline: ... in 123s`), and the
# operator must not see one before the campaign ends. So P3_QUIET_LOG=1 holds the stage library's progress line to states (it never echoes a stage's last log
# line), and of the bring-up log the driver shows only what p3_bringup_view.py allows: step results, [FAIL] lines with times redacted, the preflight VERDICT.
# The full logs are copied off the host (bringup-aN.log, campaign-rK-aN.log) and archived unread. The one exception is the log of a stage that FAILED and held the driver
# (exit 20): the driver names it in .held-logs, and p3_open_log.sh opens it, only it, writing every opening to log-openings.log and the ledger.
#
# CUDA gate (GPU hosts): the bring-up runs p3_cuda_ready.sh right after setup (a tiny CUDA init and kernel, retried for up to 10 minutes). Exit 82 of the bring-up means the
# host's GPU is not usable, and nothing else in the launch can be: the driver collects the gate's records, tears the host down, writes infrastructure_failure into the
# launch record and an infrastructure-failure line into the ledger, and exits 31. No round has run, so nothing is lost but the host's time.
#
# --resume-bringup: like --fresh for a failed bring-up, but the steps that finished ok in the previous attempt (read from its fetched log) are not run again: only
# checkout and lease always run (so a changed campaign_commit in the record takes effect), then the failed step and the ones after it. Use it when a late step failed for a
# cause that is fixed (2026-11-23: verify, on a prefix bug) and rerunning setup and generate would cost time, or fail (an A100's data fills the disk setup wants free).
#
# --stop-after-bringup: stop (exit 11, host running) once the bring-up is copied and verified, for a step a person must do before round 1
# (the harness smoke test on the pathfinder host); run the driver again without it to continue.
#
# A relaunch (amendment A1 item 2; p3_launch.sh --relaunch-of) has a record with ordinal 2: the same campaign root and launch number on its new host, its own directory
# rerun/paper3/HOST/launchN-r2 (p3_launch_dir), never the first launch's. A directory that already holds another lease's copy (a .lease marker, or a launch-record.json) is
# refused, so nothing copied from the stopped launch can be overwritten. On C-GCP the allow-list review applies to the relaunch as it does to launch 2: exit 10 until
# --config-commit, and the log carries the short review (what the new capture holds that the committed list lacks).
#
# Exit codes: 0 done and torn down; 10 C-GCP allow-list review needed; 11 stopped after bring-up on request; 20 held for a person (host kept running; the message says why);
# 21 the provider no longer lists the host; 30 the campaign stopped by its own rule (a round failed its gates twice): everything was
# copied, the host was torn down unless --on-stop hold; 31 the host's GPU is not ready (the bring-up's CUDA gate failed, e.g. CUDA error 802): an infrastructure failure
# BEFORE round 1, recorded in the launch record and the ledger, the host torn down (a relaunch is not a lost launch: no round ran); 2 usage.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
plan() { if [ -n "${P3_PLAN_CMD:-}" ]; then $P3_PLAN_CMD "$@"; else python3 "$HERE/p3_plan.py" "$@"; fi; }
die() { echo "p3_driver.sh: $*" >&2; exit 2; }

RECORD=${1:-}; shift || true
[ -f "$RECORD" ] || { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; exit 2; }
DEST="" CONFIG_COMMIT="" ON_STOP=terminate TEARDOWN=1 MAX_RESUME=2 FRESH=0 STOP_AFTER_BRINGUP=0 RESUME_BRINGUP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST=$2; shift 2 ;;
    --config-commit) CONFIG_COMMIT=$2; shift 2 ;;
    --on-stop) ON_STOP=$2; shift 2 ;;
    --no-teardown) TEARDOWN=0; shift ;;
    --max-resume) MAX_RESUME=$2; shift 2 ;;
    --fresh) FRESH=1; shift ;;
    --resume-bringup) RESUME_BRINGUP=1; FRESH=1; shift ;;
    --stop-after-bringup) STOP_AFTER_BRINGUP=1; shift ;;
    *) die "unknown option $1" ;;
  esac
done
case "$ON_STOP" in terminate|hold) ;; *) die "--on-stop is terminate or hold" ;; esac
HOST=$(record_get "$RECORD" host); LAUNCH=$(record_get "$RECORD" launch); PROVIDER=$(record_get "$RECORD" provider)
ID=$(record_get "$RECORD" instance_id); LEASE=$(record_get "$RECORD" lease_name); ZONE=$(record_get "$RECORD" zone)
[ -n "$HOST" ] && [ -n "$LAUNCH" ] && [ -n "$LEASE" ] || die "$RECORD is not a launch record"
if [ -n "$(record_get "$RECORD" terminate_confirmed_utc)" ]; then echo "$LEASE was terminated at $(record_get "$RECORD" terminate_confirmed_utc): nothing to drive"; exit 0; fi
[ -z "$CONFIG_COMMIT" ] || [ "$HOST" = c-gcp ] || die "--config-commit belongs to c-gcp only (the G12 allow-list)"
[ -z "$CONFIG_COMMIT" ] || printf '%s\n' "$CONFIG_COMMIT" | grep -Eq '^[0-9a-f]{40}$' || die "--config-commit must be a full 40-character commit hash"
ROOT_REL=$(plan root "$HOST" "$LAUNCH") || exit 2
BUDGET=$(plan budget "$HOST" "$LAUNCH") || exit 2
BRINGUP_TIMEOUT=${P3_BRINGUP_TIMEOUT:-$(echo "$BUDGET" | jq -r .bringup_s)}
ROUND_TIMEOUT=${P3_ROUND_TIMEOUT:-$(echo "$BUDGET" | jq -r .round_s)}
DEST=${DEST:-$REPO/rerun/paper3/$HOST/$(p3_launch_dir "$RECORD")}
mkdir -p "$DEST/.verified" "$DEST/.stage-scripts" "$DEST/.incoming" || die "cannot create $DEST"
# a directory is one lease's: another lease's copied launch (a relaunch of a stopped launch, or a launch that was run again) is never written into
if [ -f "$DEST/launch-record.json" ] && [ "$(jq -r '.lease_name // empty' "$DEST/launch-record.json" 2>/dev/null)" != "$LEASE" ]; then
  die "$DEST already holds the copied launch of $(jq -r '.lease_name // "another lease"' "$DEST/launch-record.json" 2>/dev/null), not of $LEASE: a relaunch has its own directory (rerun/paper3/$HOST/launch$LAUNCH-r2), a first launch is never overwritten"
fi
if [ -f "$DEST/.lease" ] && [ "$(cat "$DEST/.lease")" != "$LEASE" ]; then
  die "$DEST belongs to lease $(cat "$DEST/.lease"), not to $LEASE: use the directory of this lease"
fi
echo "$LEASE" > "$DEST/.lease"
# one driver per launch: two would poll, pack and tear down the same host
if [ -f "$DEST/.driver.pid" ] && kill -0 "$(cat "$DEST/.driver.pid" 2>/dev/null)" 2>/dev/null; then die "another driver (pid $(cat "$DEST/.driver.pid")) is running for this launch"; fi
echo $$ > "$DEST/.driver.pid"
trap 'rm -f "$DEST/.driver.pid"' EXIT
RUN_LOG="$DEST/driver-$(date -u +%Y%m%dT%H%M%SZ).log"
: > "$RUN_LOG"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$RUN_LOG"; }
p3_log() { log "$@"; }
# The library's retry budgets are sized for a probe (10 tries, 15 s apart). A launch runs for a day, so the calls at the transitions between
# stages (state, start, copy) get 40 tries, 30 s apart: about 20 minutes of continuous failure per call before the driver holds. The poll loop
# itself never gives up on failures (it checks with the provider every 60 failed polls and goes on while the host is listed).
P3_RETRIES=${P3_RETRIES:-40}; P3_RETRY_PAUSE=${P3_RETRY_PAUSE:-30}; P3_START_RETRIES=${P3_START_RETRIES:-40}
# gcloud ssh calls take seconds each, so GCP polls every minute; Lambda every 30 s (the library's default)
if [ "$PROVIDER" != lambda ]; then P3_POLL_SECONDS=${P3_POLL_SECONDS:-60}; fi
export P3_QUIET_LOG=1      # forced, not defaulted: see the header (design 7.3)
# shellcheck source=../paper3_probes/p3_stage_lib.sh
. "$P3_PROBES/p3_stage_lib.sh"

log "=== p3_driver: $HOST launch $LAUNCH, lease $LEASE, dest $DEST, on-stop $ON_STOP, teardown $TEARDOWN ==="
log "no study value is printed by this driver: only states, counts and gate outcomes (design 7.3)"
p3_keep_awake

# ---- transport ---------------------------------------------------------------------------------------------------------
if [ "$PROVIDER" = lambda ]; then
  ID_IP=$(record_get "$RECORD" ip)
  if [ -z "$ID_IP" ]; then
    "$P3_LAMBDA_SH" wait "$ID" >>"$RUN_LOG" 2>&1 || { log "the instance never became reachable; held (it is still billed)"; exit 20; }
    ID_IP=$(cat "${P3_STATE_DIR:-/tmp/p3probe}/$ID.ip") && record_set "$RECORD" ip "$ID_IP"
  fi
  p3_host_alive() { local l; l=$("$P3_LAMBDA_SH" list 2>/dev/null) || return 0; printf '%s\n' "$l" | cut -f1 | grep -qx "$ID"; }
else
  p3_host_alive() { gcp_instance_listed "$LEASE"; [ $? -ne 1 ]; }
fi
p3_transport "$RECORD" || { log "cannot set up the connection to the host; held"; exit 20; }
ready=0
for _ in $(seq 1 "${P3_SSH_WAIT_TRIES:-60}"); do
  if p3_ssh true >/dev/null 2>&1; then ready=1; break; fi
  sleep "${P3_SSH_WAIT_PAUSE:-15}"
done
[ $ready = 1 ] || { log "SSH never became ready; held (the instance is still billed; p3_teardown.sh $RECORD --yes ends it)"; exit 20; }
log "ssh ready"
p3_ssh_retry 'mkdir -p ~/p3out ~/p3stage' >/dev/null || { log "cannot create the output directories on the host; held"; exit 20; }
for f in "$P3_PROBES/p3_detach.sh" "$HERE/p3_host_pack.sh"; do
  p3_copy_to "$f" "${P3_REMOTE_PREFIX}$(basename "$f")" || { log "copying $(basename "$f") failed; held"; exit 20; }
done

# ---- stages ------------------------------------------------------------------------------------------------------------
# HOLD_LOG=NAME hold ...: the held stage's archived log (a stage that FAILED; never one that is merely slow) may be opened to diagnose it, but only through
# p3_open_log.sh, which refuses every other log and writes each opening to DEST/log-openings.log and the ledger (design 7.3: the one documented exception).
hold() {
  if [ -n "${HOLD_LOG:-}" ]; then
    echo "$HOLD_LOG" >> "$DEST/.held-logs"
    log "the log $HOLD_LOG of the failed stage is archived unread; to diagnose the failure it may be opened with: p3_open_log.sh $RECORD $HOLD_LOG (each opening is logged, design 7.3)"
  fi
  log "HELD: $*. The host keeps running; the watchdog is the backstop (lease expires $(record_get "$RECORD" expires_utc)); p3_teardown.sh $RECORD --yes ends it"; exit 20
}
gone() { log "the provider no longer lists the host ($*); nothing more to copy"; exit 21; }

stage_state() {      # STAGE: none | running | died | done:RC | unknown
  local out code started running
  out=$(p3_ssh_retry "bash \"\$HOME/p3_detach.sh\" STATUS $1") || { echo unknown; return; }
  code=$(printf '%s\n' "$out" | sed -n 's/^RC=//p' | head -n 1 | tr -d '[:space:]')
  started=$(printf '%s\n' "$out" | sed -n 's/^STARTED=//p' | head -n 1 | tr -d '[:space:]')
  running=$(printf '%s\n' "$out" | sed -n 's/^RUNNING=//p' | head -n 1 | tr -d '[:space:]')
  if [ -n "$code" ]; then echo "done:$code"; elif [ "$started" != 1 ]; then echo none; elif [ "$running" = 1 ]; then echo running; else echo died; fi
}

pick_attempt() {     # BASE: the first attempt number whose stage is not a finished failure that needs a person (--fresh skips those too)
  local base=$1 n=1 state
  while :; do
    state=$(stage_state "$base-a$n")
    case "$state" in
      done:0|done:3|none|running|unknown) echo "$n"; return ;;
      done:130|died) ;;
      done:*) [ "$FRESH" = 1 ] || { echo "$n"; return; } ;;
    esac
    n=$((n + 1))
  done
}

RESULT=""
run_stage() {        # STAGE SCRIPT_FILE TIMEOUT: start it on the host if it never was, wait for it; RESULT = exit code | died | timeout | host-gone
  local stage=$1 script=$2 timeout=$3 state
  state=$(stage_state "$stage")
  case "$state" in
    unknown) hold "cannot read the state of stage $stage (ssh kept failing)" ;;
    none)
      p3_copy_to "$script" "${P3_REMOTE_PREFIX}p3stage/$stage.sh" || hold "cannot copy the script of stage $stage"
      p3_stage_start_raw "$stage" "bash \"\$HOME/p3stage/$stage.sh\"" || hold "cannot start stage $stage" ;;
    *) log "stage $stage is already on the host ($state): attaching" ;;
  esac
  P3_STAGE_TIMEOUT=$timeout
  if p3_stage_wait "$stage"; then RESULT=$P3_STAGE_RC; else RESULT=$P3_STAGE_STATE; fi
  [ "$RESULT" = host-gone ] && gone "stage $stage"
  log "stage $stage finished: $RESULT"
  return 0
}

collect() {          # NAME PATH...: pack on the host, copy, compare the archive hash, unpack, check every file; marker on success
  local name=$1 words="" p try
  shift
  [ -f "$DEST/.verified/$name" ] && { log "pack $name already verified"; return 0; }
  for p in "$@"; do words="$words $(printf '%q' "$p")"; done
  for try in 1 2 3; do
    local stage="pack-$name-t$try" state
    state=$(stage_state "$stage")
    if [ "$state" = none ]; then
      p3_stage_start_raw "$stage" "bash \"\$HOME/p3_host_pack.sh\" $name$words" || hold "cannot start $stage"
    fi
    P3_STAGE_TIMEOUT=${P3_PACK_TIMEOUT:-3600}
    if ! p3_stage_wait "$stage" || [ "$P3_STAGE_RC" != 0 ]; then
      [ "$P3_STAGE_STATE" = host-gone ] && gone "pack $name"
      log "pack $name, try $try: the pack stage did not succeed (${P3_STAGE_RC:-$P3_STAGE_STATE})"; continue
    fi
    rm -f "$DEST/.incoming/$name.tar.gz" "$DEST/.incoming/$name.tar.gz.sha256"
    if ! P3_SCP_BOUND=${P3_PACK_FETCH_BOUND:-3600} p3_fetch "p3out/pack/$name.tar.gz" "$DEST/.incoming/" \
       || ! p3_fetch "p3out/pack/$name.tar.gz.sha256" "$DEST/.incoming/"; then
      log "pack $name, try $try: the copy failed"; continue
    fi
    local want have
    want=$(cut -d' ' -f1 "$DEST/.incoming/$name.tar.gz.sha256"); have=$(shasum -a 256 "$DEST/.incoming/$name.tar.gz" | cut -d' ' -f1)
    if [ -z "$want" ] || [ "$want" != "$have" ]; then log "pack $name, try $try: archive sha256 differs from the host's ($have vs $want)"; continue; fi
    tar -xzf "$DEST/.incoming/$name.tar.gz" -C "$DEST" || { log "pack $name, try $try: unpacking failed"; continue; }
    if ! (cd "$DEST" && shasum -a 256 -c "MANIFEST-$name.sha256" >/dev/null 2>"$DEST/.incoming/$name.check"); then
      log "pack $name, try $try: a file differs from the manifest: $(head -n 3 "$DEST/.incoming/$name.check" | tr '\n' ' ')"; continue
    fi
    date -u +%FT%TZ > "$DEST/.verified/$name"
    log "pack $name verified: archive sha256 $have, $(wc -l < "$DEST/MANIFEST-$name.sha256" | tr -d ' ') file(s) checked against the manifest"
    return 0
  done
  hold "pack $name could not be copied and verified in three tries"
}

fetch_log() {        # REMOTE_FILE: best effort, for diagnosis
  p3_fetch "p3out/$1" "$DEST/" || log "could not fetch p3out/$1"
}

bringup_view() {     # BASENAME: of the fetched bring-up log, only what p3_bringup_view.py allows (steps, [FAIL] lines without times, the VERDICT)
  python3 "$HERE/p3_bringup_view.py" "$DEST/$1" 2>&1 | while IFS= read -r line; do log "BRING-UP $line"; done
}

qa() {               # prints the allowed view of the fetched root
  python3 "$HERE/p3_qa_view.py" "$DEST/$ROOT_REL" 2>&1 | while IFS= read -r line; do log "QA $line"; done
}

teardown_host() {    # terminate and confirm (unless --no-teardown); holds when the termination cannot be confirmed
  if [ "$TEARDOWN" = 1 ]; then
    log "tearing down $LEASE"
    if p3_terminate_and_confirm "$RECORD" >>"$RUN_LOG" 2>&1; then
      p3_kill_watchdog "$RECORD"
      log "confirmed terminated at $(record_get "$RECORD" terminate_confirmed_utc)"
    else
      hold "termination of $LEASE could not be confirmed"
    fi
  else
    log "teardown skipped (--no-teardown): p3_teardown.sh $RECORD --yes ends the instance"
  fi
}

write_launch_files() {   # the launch record and one manifest of everything that arrived
  # campaign.json is written when the campaign starts and cannot hold the terminate time (design 2.6 P-BILLED): the operator's record does.
  # The address and the watchdog pid are dropped; the lease name is in the instance name the provider bills under, and in the evidence logs.
  jq 'del(.ip, .watchdog_pid)' "$RECORD" > "$DEST/launch-record.json"
  (cd "$DEST" && { find rerun -type f 2>/dev/null; echo launch-record.json; } | sort | tr '\n' '\0' | xargs -0 shasum -a 256 > LAUNCH-SHA256SUMS) \
    && log "LAUNCH-SHA256SUMS: $(wc -l < "$DEST/LAUNCH-SHA256SUMS" | tr -d ' ') file(s) (the copied campaign root and launch-record.json)"
}

infra_fail() {       # BRINGUP_LOG_NAME: the host's GPU is not ready (the CUDA gate of the bring-up failed): no round ran; record it, tear the host down, exit 31
  local log_name=$1 f detail='{}' reason code
  mkdir -p "$DEST/rerun/bringup-artifacts"
  for f in cuda-ready.json cuda-ready.jsonl cuda-ready-fabric.csv cuda-ready-nvidia-smi-q-start.txt cuda-ready-nvidia-smi-q-end.txt; do
    P3_RETRIES=2 P3_RETRY_PAUSE=2 p3_fetch "study/rerun/bringup-artifacts/$f" "$DEST/rerun/bringup-artifacts/" >/dev/null 2>&1 || true
  done
  [ -f "$DEST/rerun/bringup-artifacts/cuda-ready.json" ] && detail=$(jq -c . "$DEST/rerun/bringup-artifacts/cuda-ready.json" 2>/dev/null || echo '{}')
  reason=$(printf '%s' "$detail" | jq -r '.reason // "host GPU not ready (CUDA init failed)"')
  code=$(printf '%s' "$detail" | jq -r '.code // "unknown"')
  echo "$log_name" >> "$DEST/.held-logs"          # a failed stage's log may be opened to diagnose it, through p3_open_log.sh only (design 7.3)
  log "INFRASTRUCTURE FAILURE before round 1: $reason. No CUDA call works on this host (CUDA error $code after the gate's retries); no round has run, so a relaunch is not a lost launch (amendment A1 item 2, the lead's decision 2026-11-22). The host is torn down; the gate's records are in $DEST/rerun/bringup-artifacts and its log may be opened with: p3_open_log.sh $RECORD $log_name"
  record_set "$RECORD" infrastructure_failure "$(jq -nc --arg at "$(p3_utc)" --arg reason "$reason" --arg log "$log_name" --argjson detail "$detail" \
    '{kind: "cuda-not-ready", reason: $reason, stage: "bringup", step: "cudaready", at: $at, rounds_started: 0, bringup_log: $log, detail: $detail}')" json
  ledger_append infrastructure-failure "$RECORD" "$reason"
  teardown_host
  write_launch_files
  log "=== done: INFRASTRUCTURE FAILURE (host GPU not ready), host torn down, nothing lost: no round ran ==="
  exit 31
}

# ---- 1. bring-up -------------------------------------------------------------------------------------------------------
if [ ! -f "$DEST/.verified/bringup" ]; then
  attempt=$(pick_attempt bringup)
  skip_args=()
  if [ "$RESUME_BRINGUP" = 1 ]; then
    prev="$DEST/bringup-a$((attempt - 1)).log"
    { [ "$attempt" -gt 1 ] && [ -f "$prev" ]; } || die "--resume-bringup needs the fetched log of the previous attempt ($prev) to know which steps finished; there is none"
    skip=$(sed -nE 's/^[^ ]+ STEP ([a-z_]+) (ok|skipped)([ :].*)?$/\1/p' "$prev" | grep -vxE 'checkout|lease' | paste -sd, -)
    if [ -n "$skip" ]; then skip_args=(--skip "$skip"); log "resuming the bring-up as attempt $attempt: not repeating the steps that finished ok in attempt $((attempt - 1)): $skip"; fi
  fi
  plan bringup "$HOST" "$LAUNCH" --record "$RECORD" ${skip_args[@]+"${skip_args[@]}"} > "$DEST/.stage-scripts/bringup.sh" || die "cannot generate the bring-up script"
  run_stage "bringup-a$attempt" "$DEST/.stage-scripts/bringup.sh" "$BRINGUP_TIMEOUT"
  fetch_log "bringup-a$attempt.log"
  bringup_view "bringup-a$attempt.log"
  case "$RESULT" in
    0) ;;
    82) infra_fail "bringup-a$attempt.log" ;;
    timeout) hold "bring-up is still running after its poll budget" ;;
    *) HOLD_LOG="bringup-a$attempt.log" hold "bring-up ended with $RESULT (after the cause is fixed, run the driver again with --fresh, or with --resume-bringup to skip the steps that finished ok)" ;;
  esac
  collect bringup rerun/setup-logs 'rerun/verify-*.json' rerun/preflight-bringup rerun/bringup-artifacts
fi

if [ "$STOP_AFTER_BRINGUP" = 1 ]; then
  log "bring-up is verified (--stop-after-bringup): the host keeps running; run the driver again without the flag to start round $(plan rounds "$HOST" "$LAUNCH" | head -n 1)"
  exit 11
fi

# ---- 2. C-GCP: the G12 allow-list is reviewed by a person, once, before the formal start -------------------------------------
if [ "$HOST" = c-gcp ] && [ ! -f "$DEST/.verified/config" ]; then
  if [ -z "$CONFIG_COMMIT" ]; then
    CAMPAIGN_COMMIT=$(record_get "$RECORD" campaign_commit)
    if review=$(plan allowlist-review --capture "$DEST/rerun/bringup-artifacts/exclusivity-after.json" --commit "$CAMPAIGN_COMMIT" 2>/dev/null); then
      # the list is committed already (launch 2, a relaunch): the review is the short one, and the driver has made the comparison; a person still decides
      new_items=$(printf '%s' "$review" | jq -r '.not_in_list | if length == 0 then "none" else join(", ") end')
      log "REVIEW REQUIRED (the allow-list is committed at $CAMPAIGN_COMMIT; short review): the capture $DEST/rerun/bringup-artifacts/exclusivity-after.json holds $(printf '%s' "$review" | jq -r .capture_items) item(s); not in the committed list: $new_items"
      log "none: run p3_driver.sh $RECORD --config-commit $CAMPAIGN_COMMIT. Any item named: mask it on the host before round 1 and never add it to the list (docs/PAPER3_RUNBOOK.md section 6, 'Launch 2 of C-GCP, or a relaunch')."
    else
      log "REVIEW REQUIRED: read $DEST/rerun/bringup-artifacts/exclusivity-after.json (and -before.json), decide the allow-list, commit it to paper3_hosts.json"
      log "(and append the section 12 amendment), push, then run: p3_driver.sh $RECORD --config-commit <that commit>. See docs/PAPER3_RUNBOOK.md, 'C-GCP allow-list'."
    fi
    exit 10
  fi
  plan check-allowlist --old "$(record_get "$RECORD" campaign_commit)" --new "$CONFIG_COMMIT" >> "$RUN_LOG" 2>&1 || die "the change from the campaign commit to $CONFIG_COMMIT is not just the allow-list (see the log)"
  cat > "$DEST/.stage-scripts/syncconfig.sh" <<EOF
#!/usr/bin/env bash
set -eu
cd "\$HOME/study"
git fetch --tags origin
git checkout --detach $CONFIG_COMMIT
test "\$(git rev-parse HEAD)" = $CONFIG_COMMIT
test -z "\$(git status --porcelain)"
echo "config commit $CONFIG_COMMIT checked out"
EOF
  run_stage syncconfig "$DEST/.stage-scripts/syncconfig.sh" 900
  [ "$RESULT" = 0 ] || hold "syncing the host to $CONFIG_COMMIT ended with $RESULT"
  record_set "$RECORD" config_commit "$CONFIG_COMMIT"
  date -u +%FT%TZ > "$DEST/.verified/config"
fi

# ---- 3. the rounds -----------------------------------------------------------------------------------------------------------
ROUNDS=$(plan rounds "$HOST" "$LAUNCH") || die "cannot list the rounds"
LAST=$(printf '%s\n' "$ROUNDS" | tail -n 1)
STOPPED=0
for K in $ROUNDS; do
  [ -f "$DEST/.verified/round-$K" ] && { log "round $K already verified: skipping"; continue; }
  attempt=$(pick_attempt "campaign-r$K")
  while :; do
    stage="campaign-r$K-a$attempt"
    if [ "$K" = "$LAST" ]; then stop=(); else stop=(--stop-after "$K"); fi
    plan stage-script "$HOST" "$LAUNCH" --record "$RECORD" ${stop[@]+"${stop[@]}"} > "$DEST/.stage-scripts/$stage.sh" || die "cannot generate the stage script"
    run_stage "$stage" "$DEST/.stage-scripts/$stage.sh" "$ROUND_TIMEOUT"
    fetch_log "$stage.log"
    case "$RESULT" in
      0) break ;;
      3) STOPPED=1; break ;;
      130|died)
        if [ "$attempt" -le "$MAX_RESUME" ]; then attempt=$((attempt + 1)); log "round $K: $RESULT; resuming (attempt $attempt)"; continue; fi
        HOLD_LOG="$stage.log" hold "round $K was interrupted $attempt times" ;;
      timeout) hold "round $K is still running after its poll budget; run the driver again to attach, nothing was killed" ;;
      4) HOLD_LOG="$stage.log" hold "round $K: the gates could not be evaluated (exit 4, not counted as a failed round): fix the host config on the host, then run the driver again with --fresh" ;;
      5) HOLD_LOG="$stage.log" hold "round $K: the campaign refused to start for a design reason (exit 5); after the cause is fixed, run the driver again with --fresh" ;;
      6) HOLD_LOG="$stage.log" hold "round $K: the lease is not valid for this invocation (exit 6); extend it (docs/PAPER3_RUNBOOK.md) and run the driver again with --fresh" ;;
      *) HOLD_LOG="$stage.log" hold "round $K ended with exit $RESULT; after the cause is fixed, run the driver again with --fresh" ;;
    esac
  done
  collect "round-$K" "$ROOT_REL/round-$K" "$ROOT_REL/round-$K-repeat-*" "$ROOT_REL/campaign.json" "$ROOT_REL/invocations.jsonl" "$ROOT_REL/status.json"
  qa
  if [ "$STOPPED" = 1 ]; then
    log "the campaign stopped by its own rule at round $K (a round and its repeat both failed their gates)"
    collect stopped "$ROOT_REL"
    break
  fi
  state=$(python3 "$HERE/p3_qa_view.py" "$DEST/$ROOT_REL" --json | jq -r .state)
  case "$state" in paused|complete) ;; *) hold "round $K exited 0 but status.json says '$state'" ;; esac
  date -u +%FT%TZ > "$DEST/.verified/round-$K"
done

# ---- 4. inventory: nothing on the host is missing here ----------------------------------------------------------------------
INVENTORY_PY='
import hashlib, os, sys
root = sys.argv[1]
rows = []
for base, dirs, files in os.walk(root):
    for name in files:
        if name == ".campaign.lock":
            continue
        path = os.path.join(base, name)
        rows.append("%s %d" % (os.path.relpath(path, root), os.path.getsize(path)))
rows.sort()
print(len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
'
if [ "$STOPPED" = 0 ]; then
  remote=$(p3_ssh_retry "cd \"\$HOME/study\" && python3 -c '$INVENTORY_PY' \"$ROOT_REL\"") || hold "cannot take the inventory of the campaign root on the host"
  local_inv=$(python3 -c "$INVENTORY_PY" "$DEST/$ROOT_REL")
  if [ "$remote" != "$local_inv" ]; then hold "inventory differs: host '$remote', here '$local_inv' (files on the host that were not copied, or the reverse)"; fi
  log "inventory: the campaign root on the host and the copy here hold the same files ($remote)"
fi

# ---- 5. teardown -------------------------------------------------------------------------------------------------------------
if [ "$STOPPED" = 1 ] && [ "$ON_STOP" = hold ]; then hold "stopped by rule, --on-stop hold"; fi
teardown_host
# ---- 6. the launch record and one manifest of everything that arrived ------------------------------------------------------------------
write_launch_files
if [ "$STOPPED" = 1 ]; then log "=== done: STOPPED by the campaign's own rule; all data copied ==="; exit 30; fi
log "=== done: $HOST launch $LAUNCH complete ==="
exit 0
