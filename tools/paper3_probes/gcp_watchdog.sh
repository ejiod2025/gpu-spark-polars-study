#!/usr/bin/env bash
# gcp_watchdog.sh NAME ZONE SECONDS: second layer behind --max-run-duration. After SECONDS, delete the instance NAME in ZONE,
# but only if the name starts with p3- and it carries the label purpose=paper3-power-probe. Run it detached.
# Retries both the existence check and the delete call (a transient gcloud/API error must never be read as "already
# deleted"; it is distinguished from a genuine empty listing and retried instead).
set -uo pipefail
name=$1 zone=$2
case "$name" in p3-*) ;; *) echo "refusing: $name is not named p3-*" >&2; exit 1 ;; esac
sleep "$3"
echo "$(date -u +%FT%TZ) watchdog deadline reached for $name; checking"
errfile=$(mktemp)
trap 'rm -f "$errfile"' EXIT
for attempt in 1 2 3 4 5 6; do
  found=$(gcloud compute instances list --filter="name=$name AND labels.purpose=paper3-power-probe" --format='value(name)' 2>"$errfile")
  list_rc=$?
  if [ $list_rc -ne 0 ]; then
    echo "$(date -u +%FT%TZ) list query failed (attempt $attempt, rc=$list_rc): $(cat "$errfile")" >&2
    sleep 15
    continue
  fi
  if [ "$found" != "$name" ]; then
    echo "$(date -u +%FT%TZ) $name not found (already deleted)"
    exit 0
  fi
  if gcloud compute instances delete "$name" --zone="$zone" --quiet; then
    echo "$(date -u +%FT%TZ) deleted $name"
    exit 0
  fi
  echo "$(date -u +%FT%TZ) delete attempt $attempt failed for $name; retrying in 15s" >&2
  sleep 15
done
echo "$(date -u +%FT%TZ) WARNING: could not confirm deletion of $name after retries; manual check required" >&2
exit 1
