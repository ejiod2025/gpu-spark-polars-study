#!/usr/bin/env bash
# run_gcp_cpu_probe.sh [ZONE ...]
#
# Pre-lock P6-CPU probe on GCP c3-highmem-192-metal, end to end with no dependency on an operator returning mid-run:
# create the instance (p3- name with owner and expiry, label purpose=paper3-power-probe, --max-run-duration and
# DELETE on expiry) -> arm gcp_watchdog.sh as a second layer -> wait for SSH -> copy the repository at HEAD (git archive,
# so the probe code is exactly the committed code) -> run `p3_cpu_probes.py p6-cpu` DETACHED on the host (p3_detach.sh:
# own session, all descriptors redirected, the start call returns at once; no long-lived SSH session) -> poll with short,
# time-bounded SSH calls for its exit-code file (a failed poll is retried, never fatal: the run survives a sleeping laptop) ->
# copy p6-cpu.json, the 200 ms series, the sampler sidecar and the probe log into docs/paper3-evidence/probes/ with
# sha256 -> delete the instance -> confirm it is gone. See p3_stage_lib.sh. The watchdog is a Mac process too: it does not
# run, and nothing here can delete the instance, while the Mac sleeps (caffeinate keeps it awake while the run lasts).
# Synthetic CPU load only; no study query. Only the instance this script creates is ever touched (by its own name).
# Zones are tried in order until one has capacity (default: us-east4-a, the design's C-GCP zone, then other US zones).
set -uo pipefail

ZONES=("$@")
[ ${#ZONES[@]} -eq 0 ] && ZONES=(us-east4-a us-east4-b us-east4-c us-central1-a us-central1-b us-central1-c)
MACHINE=c3-highmem-192-metal
MAX_RUN=75m
WATCHDOG_S=4500          # 75 min, the same as --max-run-duration

HERE=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$HERE/../.." && pwd)
EVIDENCE_DIR="$REPO_ROOT/docs/paper3-evidence/probes"
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
EXPIRES=$(date -u -v+75M +%Y%m%dt%H%Mz 2>/dev/null || date -u -d '+75 min' +%Y%m%dt%H%Mz)
NAME="p3-probe-c3hm-owner-ejiod-expires-${EXPIRES}"
PREFIX="gcp-${MACHINE}-${STAMP}-p6-cpu"
RUN_LOG="$HERE/run-gcp-p6cpu-${STAMP}.log"
STAGING="$HERE/.staging-gcp-p6cpu-${STAMP}"
SHAFILE="$EVIDENCE_DIR/${PREFIX}-SHA256SUMS.txt"
mkdir -p "$STAGING" "$EVIDENCE_DIR"
: > "$RUN_LOG"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$RUN_LOG"; }
p3_log() { log "$@"; }
# shellcheck source=p3_stage_lib.sh
. "$HERE/p3_stage_lib.sh"
P3_POLL_SECONDS=${P3_POLL_SECONDS:-60}        # gcloud compute ssh takes seconds per call

log "=== P6-CPU probe: $MACHINE, name $NAME, zones [${ZONES[*]}] ==="
log "no study queries will be run (synthetic pinned CPU spin loops only)"
p3_keep_awake

ZONE=""
for z in "${ZONES[@]}"; do
  log "trying $z"
  if gcloud compute instances create "$NAME" --zone="$z" --machine-type="$MACHINE" \
       --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
       --boot-disk-size=50GB --boot-disk-type=hyperdisk-balanced \
       --maintenance-policy=TERMINATE --max-run-duration="$MAX_RUN" --instance-termination-action=DELETE \
       --labels="owner=ejiod,purpose=paper3-power-probe,expires=${EXPIRES}" >>"$RUN_LOG" 2>&1; then
    ZONE=$z; break
  fi
  log "create failed in $z (see log); next zone"
done
if [ -z "$ZONE" ]; then log "NO CAPACITY in any zone; nothing to delete"; exit 1; fi
LAUNCH_EPOCH=$(date +%s)
log "created $NAME in $ZONE"

nohup "$HERE/gcp_watchdog.sh" "$NAME" "$ZONE" "$WATCHDOG_S" >>"$HERE/watchdog-gcp-p6cpu-${STAMP}.log" 2>&1 &
WATCHDOG_PID=$!; disown
log "watchdog armed pid=$WATCHDOG_PID deadline_s=$WATCHDOG_S (wall-clock deadline; backstop, this script deletes the instance itself)"
p3_keep_awake "$WATCHDOG_PID" "the watchdog"

SSHFLAGS=(--ssh-flag=-oConnectTimeout=15 --ssh-flag=-oServerAliveInterval=10 --ssh-flag=-oServerAliveCountMax=3
          --ssh-flag=-oStrictHostKeyChecking=no --ssh-flag=-oUserKnownHostsFile=/dev/null --ssh-flag=-oLogLevel=ERROR)
SCPFLAGS=("${SSHFLAGS[@]/--ssh-flag=/--scp-flag=}")
P3_SSH=(gcloud compute ssh "$NAME" --zone="$ZONE" --quiet "${SSHFLAGS[@]}" --command)
P3_SCP_FROM=(gcloud compute scp --zone="$ZONE" --quiet --recurse "${SCPFLAGS[@]}")
P3_SCP_TO=(gcloud compute scp --zone="$ZONE" --quiet "${SCPFLAGS[@]}")
P3_REMOTE_PREFIX="$NAME:"
p3_host_alive() {        # 0 unless the API answers and does not list the instance (an API failure is "unknown": alive)
  local found
  found=$(gcloud compute instances list --filter="name=$NAME" --format='value(name)' 2>>"$RUN_LOG") || return 0
  [ -n "$found" ]
}

DELETED=0
delete_and_confirm() {
  [ "$DELETED" = 1 ] && return 0
  DELETED=1
  log "deleting $NAME ($ZONE)"
  gcloud compute instances delete "$NAME" --zone="$ZONE" --quiet >>"$RUN_LOG" 2>&1
  local deadline=$(( $(date +%s) + 600 ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    local found rc
    found=$(gcloud compute instances list --filter="name=$NAME" --format='value(name)' 2>>"$RUN_LOG"); rc=$?
    if [ $rc -eq 0 ] && [ -z "$found" ]; then log "confirmed deleted: $NAME no longer listed"; kill "$WATCHDOG_PID" 2>/dev/null; return 0; fi
    sleep 15
  done
  log "WARNING: could not confirm deletion of $NAME within 10 minutes; watchdog pid $WATCHDOG_PID still armed"
  return 1
}
publish_run_log() {   # redacted copy: only this script's timestamped lines, IPs masked
  grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+Z ' "$RUN_LOG" | sed -E 's/([0-9]{1,3}\.){3}[0-9]{1,3}/<ip>/g' > "$EVIDENCE_DIR/${PREFIX}-run.log"
}
on_exit() { delete_and_confirm; publish_run_log; rm -rf "$STAGING"; }
trap on_exit EXIT

ready=0
for _ in $(seq 1 40); do
  if p3_ssh true >/dev/null 2>&1; then ready=1; break; fi
  sleep 15
done
[ $ready = 1 ] || { log "SSH never became ready; aborting (delete runs via the exit trap)"; exit 1; }
log "ssh ready ($(( $(date +%s) - LAUNCH_EPOCH )) s after create)"

ARCHIVE="$STAGING/repo-$(git -C "$REPO_ROOT" rev-parse --short HEAD).tar.gz"
git -C "$REPO_ROOT" archive --format=tar.gz -o "$ARCHIVE" HEAD
log "repository archive of HEAD $(git -C "$REPO_ROOT" rev-parse HEAD) ($(wc -c < "$ARCHIVE" | tr -d ' ') bytes)"
P3_SCP_BOUND=1200 p3_copy_to "$ARCHIVE" "$NAME:repo.tar.gz" || { log "copying the repository failed; aborting"; exit 1; }
p3_ssh_retry 'rm -rf ~/gpu-spark-polars-study && mkdir -p ~/gpu-spark-polars-study ~/p3out && tar -xzf ~/repo.tar.gz -C ~/gpu-spark-polars-study' >/dev/null \
  || { log "unpacking the repository failed; aborting"; exit 1; }
# the detach helper from the working tree (it need not be in HEAD's archive)
p3_copy_to "$HERE/p3_detach.sh" "$NAME:p3_detach.sh" || { log "copying p3_detach.sh failed; aborting"; exit 1; }

# The probe needs root (RAPL); it runs as one detached stage: the output directory is handed back to the login user at the end.
STAGE_SCRIPT='cd "$1/gpu-spark-polars-study" && rm -rf "$1/p6cpu" && python3 tools/paper3_probes/p3_cpu_probes.py p6-cpu --out "$1/p6cpu"; rc=$?; chown -R "$2:$3" "$1/p6cpu" 2>/dev/null; exit $rc'
p3_stage_start_raw p6cpu "sudo bash -c '$STAGE_SCRIPT' _ \"\$HOME\" \"\$(id -u)\" \"\$(id -g)\"" \
  || { log "could not start the probe; aborting"; exit 1; }
log "p6-cpu started detached on the host"

P3_STAGE_TIMEOUT=${P3_STAGE_TIMEOUT:-3300}
if p3_stage_wait p6cpu; then RC=$P3_STAGE_RC; else RC=$P3_STAGE_STATE; fi
log "probe exit marker: ${RC:-<none>}"

p3_fetch "p6cpu" "$STAGING/" || log "WARNING: could not fetch the p6cpu output directory"
p3_fetch "p3out/p6cpu.log" "$STAGING/" || log "WARNING: could not fetch the probe log"
shopt -s nullglob
for src in "$STAGING"/p6cpu/* "$STAGING"/p6cpu.log; do
  base=$(basename "$src"); dst="$EVIDENCE_DIR/gcp-${MACHINE}-${STAMP}-${base}"
  case $base in p6cpu.log) dst="$EVIDENCE_DIR/${PREFIX}.log" ;; esac
  if [ -s "$src" ]; then
    cp "$src" "$dst"
    echo "$(shasum -a 256 "$dst" | awk '{print $1}')  $(basename "$dst")  $(wc -c < "$dst" | tr -d ' ')B" >> "$SHAFILE"
    log "collected $(basename "$dst")"
  else
    log "WARNING: $base missing or empty (not collected)"
  fi
done
log "=== done: exit marker ${RC:-none}; deletion follows via the exit trap ==="
[ "$RC" = 0 ]
