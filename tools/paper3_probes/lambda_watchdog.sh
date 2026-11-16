#!/usr/bin/env bash
# lambda_watchdog.sh ID SECONDS: once SECONDS of WALL-CLOCK time have passed, terminate the p3- instance ID through the API
# (deadline <= 2 h). Run it detached.
#
# The deadline is an epoch time computed when the watchdog is armed (and recorded: the first log line, and
# /tmp/p3probe/ID.watchdog_deadline); the loop sleeps 30 s at a time and compares the clock with it. A single `sleep SECONDS`
# does not do this: a Mac's sleep does not count the time the machine is asleep, so after a sleep it would fire hours late
# (the 2026-11-16 B200 run: armed with 5400 s, never fired in the 3 h 10 min until the driver terminated the host itself).
# Compared with the wall clock, it fires at the first wake past the deadline.
#
# Then issues terminate, polls /instances (via lambda.sh list) until ID is gone or 10 minutes have passed, re-issuing
# terminate every 30s while it is still listed. Lambda's own eventual consistency can leave a terminated
# instance showing status "terminating" in /instances for longer than a short fixed retry count would tolerate (seen
# in practice: gone well within 10 minutes, but not within ~90s), so "still listed" is never read as "could not
# terminate" until the full 10-minute window elapses. Uses lambda.sh, so it can only ever terminate an instance named
# p3-*.
#
# Tests inject the clock and the sleep: P3_WATCHDOG_NOW (a command printing epoch seconds), P3_WATCHDOG_SLEEP (a command taking
# seconds), P3_WATCHDOG_TICK (30), P3_STATE_DIR (/tmp/p3probe).
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
id=$1
state=${P3_STATE_DIR:-/tmp/p3probe}
tick=${P3_WATCHDOG_TICK:-30}
now() { ${P3_WATCHDOG_NOW:-date +%s}; }
wsleep() { ${P3_WATCHDOG_SLEEP:-sleep} "$1"; }
stamp() { date -u +%FT%TZ; }

deadline=$(( $(now) + $2 ))
mkdir -p "$state" 2>/dev/null && echo "$deadline" > "$state/$id.watchdog_deadline" 2>/dev/null
echo "$(stamp) watchdog armed for $id: deadline_epoch=$deadline (in $2 s of wall-clock time; checked every ${tick} s)"
while [ "$(now)" -lt "$deadline" ]; do
  wsleep "$tick"
done
late=$(( $(now) - deadline ))
echo "$(stamp) watchdog deadline reached for $id (${late} s past it); terminating"
if [ "$late" -gt $(( 2 * tick )) ]; then
  echo "$(stamp) the deadline passed while this machine was asleep or suspended; acting at the first check after it woke"
fi
"$here/lambda.sh" terminate "$id" || echo "$(stamp) initial terminate call returned a failure (will keep polling)" >&2
poll_until=$(( $(now) + 600 ))
last_reissue=$(now)
while [ "$(now)" -lt "$poll_until" ]; do
  listing=$("$here/lambda.sh" list)
  list_rc=$?
  if [ $list_rc -eq 0 ] && ! printf '%s\n' "$listing" | cut -f1 | grep -qx "$id"; then
    echo "$(stamp) $id is no longer listed among p3- instances (terminated)"
    exit 0
  fi
  at=$(now)
  if [ $list_rc -ne 0 ]; then
    echo "$(stamp) list query failed (rc=$list_rc); will retry" >&2
  elif [ $((at - last_reissue)) -ge 30 ]; then
    "$here/lambda.sh" terminate "$id" >/dev/null 2>&1 || true
    last_reissue=$at
  fi
  wsleep 15
done
echo "$(stamp) WARNING: $id still listed after 10 minutes of polling; manual check required" >&2
exit 1
