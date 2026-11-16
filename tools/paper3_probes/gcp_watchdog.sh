#!/usr/bin/env bash
# gcp_watchdog.sh NAME ZONE SECONDS: second layer behind --max-run-duration. Once SECONDS of WALL-CLOCK time have passed, delete
# the instance NAME in ZONE, but only if the name starts with p3- and it carries the label purpose=paper3-power-probe. Run it
# detached.
#
# The deadline is an epoch time computed when the watchdog is armed (and recorded: the first log line, and
# /tmp/p3probe/NAME.watchdog_deadline); the loop sleeps 30 s at a time and compares the clock with it, so a laptop that slept
# through the deadline fires it at the first wake instead of hours late (a single `sleep SECONDS` does not count the time
# the machine is asleep). Retries both the existence check and the delete call (a transient gcloud/API error must never be read
# as "already deleted"; it is distinguished from a genuine empty listing and retried instead).
#
# Tests inject the clock and the sleep: P3_WATCHDOG_NOW, P3_WATCHDOG_SLEEP, P3_WATCHDOG_TICK (30), P3_STATE_DIR (/tmp/p3probe).
set -uo pipefail
name=$1 zone=$2
case "$name" in p3-*) ;; *) echo "refusing: $name is not named p3-*" >&2; exit 1 ;; esac
state=${P3_STATE_DIR:-/tmp/p3probe}
tick=${P3_WATCHDOG_TICK:-30}
now() { ${P3_WATCHDOG_NOW:-date +%s}; }
wsleep() { ${P3_WATCHDOG_SLEEP:-sleep} "$1"; }
stamp() { date -u +%FT%TZ; }

deadline=$(( $(now) + $3 ))
mkdir -p "$state" 2>/dev/null && echo "$deadline" > "$state/$name.watchdog_deadline" 2>/dev/null
echo "$(stamp) watchdog armed for $name: deadline_epoch=$deadline (in $3 s of wall-clock time; checked every ${tick} s)"
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
  found=$(gcloud compute instances list --filter="name=$name AND labels.purpose=paper3-power-probe" --format='value(name)' 2>"$errfile")
  list_rc=$?
  if [ $list_rc -ne 0 ]; then
    echo "$(stamp) list query failed (attempt $attempt, rc=$list_rc): $(cat "$errfile")" >&2
    wsleep 15
    continue
  fi
  if [ "$found" != "$name" ]; then
    echo "$(stamp) $name not found (already deleted)"
    exit 0
  fi
  if gcloud compute instances delete "$name" --zone="$zone" --quiet; then
    echo "$(stamp) deleted $name"
    exit 0
  fi
  echo "$(stamp) delete attempt $attempt failed for $name; retrying in 15s" >&2
  wsleep 15
done
echo "$(stamp) WARNING: could not confirm deletion of $name after retries; manual check required" >&2
exit 1
