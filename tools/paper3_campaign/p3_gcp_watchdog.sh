#!/usr/bin/env bash
# p3_gcp_watchdog.sh NAME ZONE SECONDS: the campaign twin of tools/paper3_probes/gcp_watchdog.sh, second layer behind --max-run-duration.
# Once SECONDS of WALL-CLOCK time have passed, delete (or stop: see below) the instance NAME in ZONE, but only if the name starts with p3- and it carries
# the label project=paper3-energy. Run it detached (p3_launch.sh does).
#
# Why a twin and not the probe watchdog: that one deletes only instances labelled purpose=paper3-power-probe, so on a campaign
# host it would find nothing and report "already deleted" (exit 0) without deleting anything. This one filters on the label every
# campaign instance carries (project=paper3-energy; the project also holds instances that are not ours, so the filter is the guard).
#
# The deadline is an epoch time computed when the watchdog is armed (first log line, and $P3_STATE_DIR/NAME.watchdog_deadline); the
# loop sleeps 30 s at a time and compares the clock with it, so a laptop that slept through the deadline fires it at the first wake
# (a single `sleep SECONDS` does not count the time the machine is asleep). A failed gcloud call is never read as "already deleted":
# it is retried. Tests inject P3_WATCHDOG_NOW, P3_WATCHDOG_SLEEP, P3_WATCHDOG_TICK (30) and P3_STATE_DIR (/tmp/p3probe).
set -uo pipefail
name=${1:?NAME} zone=${2:?ZONE} seconds=${3:?SECONDS}
case "$name" in p3-*) ;; *) echo "refusing: $name is not named p3-*" >&2; exit 1 ;; esac
export CLOUDSDK_CORE_PROJECT=${P3_GCP_PROJECT:-siriusmedai}      # the shared project, whatever the operator's gcloud default is
action=${P3_GCP_WATCHDOG_ACTION:-delete}
case "$action" in delete|stop) ;; *) echo "refusing: P3_GCP_WATCHDOG_ACTION is delete or stop, not '$action'" >&2; exit 1 ;; esac
state=${P3_STATE_DIR:-/tmp/p3probe}
tick=${P3_WATCHDOG_TICK:-30}
now() { ${P3_WATCHDOG_NOW:-date +%s}; }
wsleep() { ${P3_WATCHDOG_SLEEP:-sleep} "$1"; }
stamp() { date -u +%FT%TZ; }

deadline=$(( $(now) + seconds ))
mkdir -p "$state" 2>/dev/null && echo "$deadline" > "$state/$name.watchdog_deadline" 2>/dev/null
echo "$(stamp) watchdog armed for $name: deadline_epoch=$deadline (in $seconds s of wall-clock time; checked every ${tick} s)"
while [ "$(now)" -lt "$deadline" ]; do
  wsleep "$tick"
done
late=$(( $(now) - deadline ))
echo "$(stamp) watchdog deadline reached for $name (${late} s past it); checking"
if [ "$late" -gt $(( 2 * tick )) ]; then
  echo "$(stamp) the deadline passed while this machine was asleep or suspended; acting at the first check after it woke"
fi
errfile=$(mktemp)
trap 'rm -f "$errfile"' EXIT
for attempt in 1 2 3 4 5 6; do
  found=$(gcloud compute instances list --filter="name=$name AND labels.project=paper3-energy" --format='value(name,status)' 2>"$errfile")
  list_rc=$?
  if [ $list_rc -ne 0 ]; then
    echo "$(stamp) list query failed (attempt $attempt, rc=$list_rc): $(cat "$errfile")" >&2
    wsleep 15
    continue
  fi
  status=""
  case "$found" in *$'\t'*) status=${found#*$'\t'}; found=${found%%$'\t'*} ;; esac
  if [ "$found" != "$name" ]; then
    echo "$(stamp) $name not found among instances labelled project=paper3-energy (already deleted)"
    exit 0
  fi
  if [ "$action" = stop ]; then
    case "$status" in TERMINATED|STOPPING|SUSPENDING|SUSPENDED)
      echo "$(stamp) $name is already $status (stopped by the provider's own limit or by hand); its disk is kept"; exit 0 ;;
    esac
    if gcloud compute instances stop "$name" --zone="$zone" --quiet; then
      echo "$(stamp) stopped $name (the disk is kept; compute is no longer billed)"
      exit 0
    fi
    echo "$(stamp) stop attempt $attempt failed for $name; retrying in 15s" >&2
  else
    if gcloud compute instances delete "$name" --zone="$zone" --quiet; then
      echo "$(stamp) deleted $name"
      exit 0
    fi
    echo "$(stamp) delete attempt $attempt failed for $name; retrying in 15s" >&2
  fi
  wsleep 15
done
echo "$(stamp) WARNING: could not confirm that $name was ${action}d after retries; manual check required" >&2
exit 1
