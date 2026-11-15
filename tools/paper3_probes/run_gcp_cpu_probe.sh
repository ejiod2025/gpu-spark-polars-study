#!/usr/bin/env bash
# run_gcp_cpu_probe.sh [ZONE ...]
#
# Pre-lock P6-CPU probe on GCP c3-highmem-192-metal, end to end with no dependency on an operator returning mid-run:
# create the instance (p3- name with owner and expiry, label purpose=paper3-power-probe, --max-run-duration and
# DELETE on expiry) -> arm gcp_watchdog.sh as a second layer -> wait for SSH -> copy the repository at HEAD (git archive,
# so the probe code is exactly the committed code) -> run `p3_cpu_probes.py p6-cpu` DETACHED on the host (no long-lived
# SSH session) -> poll with short SSH calls for its exit marker -> copy p6-cpu.json, the 200 ms series, the sampler
# sidecar and the probe log into docs/paper3-evidence/probes/ with sha256 -> delete the instance -> confirm it is gone.
# Synthetic CPU load only; no study query. Only the instance this script creates is ever touched (by its own name).
# Zones are tried in order until one has capacity (default: us-east4-a, the design's C-GCP zone, then other US zones).
set -uo pipefail

ZONES=("$@")
[ ${#ZONES[@]} -eq 0 ] && ZONES=(us-east4-a us-east4-b us-east4-c us-central1-a us-central1-b us-central1-c)
MACHINE=c3-highmem-192-metal
MAX_RUN=75m
WATCHDOG_S=4500          # 75 min, the same as --max-run-duration
POLL_END_S=3600          # stop polling 60 min after launch

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

log "=== P6-CPU probe: $MACHINE, name $NAME, zones [${ZONES[*]}] ==="
log "no study queries will be run (synthetic pinned CPU spin loops only)"

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
log "watchdog armed pid=$WATCHDOG_PID deadline_s=$WATCHDOG_S (backstop; this script deletes the instance itself)"

SSHFLAGS=(--ssh-flag=-oConnectTimeout=15 --ssh-flag=-oServerAliveInterval=10 --ssh-flag=-oServerAliveCountMax=3
          --ssh-flag=-oStrictHostKeyChecking=no --ssh-flag=-oUserKnownHostsFile=/dev/null --ssh-flag=-oLogLevel=ERROR)
rssh() { gcloud compute ssh "$NAME" --zone="$ZONE" --quiet "${SSHFLAGS[@]}" --command "$1" 2>>"$RUN_LOG"; }
rscp_from() { gcloud compute scp --zone="$ZONE" --quiet --recurse "${SSHFLAGS[@]/--ssh-flag=/--scp-flag=}" "$NAME:$1" "$2" >>"$RUN_LOG" 2>&1; }
rscp_to() { gcloud compute scp --zone="$ZONE" --quiet "${SSHFLAGS[@]/--ssh-flag=/--scp-flag=}" "$1" "$NAME:$2" >>"$RUN_LOG" 2>&1; }

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
  if rssh 'true' >/dev/null; then ready=1; break; fi
  sleep 15
done
[ $ready = 1 ] || { log "SSH never became ready; aborting (delete runs via the exit trap)"; exit 1; }
log "ssh ready ($(( $(date +%s) - LAUNCH_EPOCH )) s after create)"

ARCHIVE="$STAGING/repo-$(git -C "$REPO_ROOT" rev-parse --short HEAD).tar.gz"
git -C "$REPO_ROOT" archive --format=tar.gz -o "$ARCHIVE" HEAD
log "repository archive of HEAD $(git -C "$REPO_ROOT" rev-parse HEAD) ($(wc -c < "$ARCHIVE" | tr -d ' ') bytes)"
rscp_to "$ARCHIVE" "repo.tar.gz" || { log "copying the repository failed; aborting"; exit 1; }
rssh 'rm -rf ~/gpu-spark-polars-study && mkdir -p ~/gpu-spark-polars-study && tar -xzf ~/repo.tar.gz -C ~/gpu-spark-polars-study' \
  || { log "unpacking the repository failed; aborting"; exit 1; }

rssh 'cd ~/gpu-spark-polars-study && rm -rf ~/p6cpu ~/p6cpu.done && nohup sudo bash -c "python3 tools/paper3_probes/p3_cpu_probes.py p6-cpu --out $HOME/p6cpu > $HOME/p6cpu.log 2>&1; echo \$? > $HOME/p6cpu.done; chown -R $(id -u):$(id -g) $HOME/p6cpu $HOME/p6cpu.log $HOME/p6cpu.done" > /dev/null 2>&1 < /dev/null & disown; echo started' \
  | grep -q started || { log "could not start the probe; aborting"; exit 1; }
log "p6-cpu started detached on the host"

RC=""
while [ $(( $(date +%s) - LAUNCH_EPOCH )) -lt $POLL_END_S ]; do
  sleep 60
  RC=$(rssh 'cat ~/p6cpu.done 2>/dev/null' | tr -d '[:space:]')
  [ -n "$RC" ] && break
  last=$(rssh 'tail -1 ~/p6cpu.log 2>/dev/null' | cut -c1-160)
  log "running; last log line: ${last:-<none>}"
done
log "probe exit marker: ${RC:-<none: poll deadline reached>}"

rscp_from "~/p6cpu" "$STAGING/" ; rscp_from "~/p6cpu.log" "$STAGING/"
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
