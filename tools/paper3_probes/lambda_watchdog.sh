#!/usr/bin/env bash
# lambda_watchdog.sh ID SECONDS: after SECONDS, terminate the p3- instance ID through the API (deadline <= 2 h). Run it
# detached. Issues terminate, then polls /instances (via lambda.sh list) until ID is gone or 10 minutes have passed,
# re-issuing terminate every 30s while it is still listed. Lambda's own eventual consistency can leave a terminated
# instance showing status "terminating" in /instances for longer than a short fixed retry count would tolerate (seen
# in practice: gone well within 10 minutes, but not within ~90s), so "still listed" is never read as "could not
# terminate" until the full 10-minute window elapses. Uses lambda.sh, so it can only ever terminate an instance named
# p3-*.
set -uo pipefail
here=$(cd "$(dirname "$0")" && pwd)
id=$1
sleep "$2"
echo "$(date -u +%FT%TZ) watchdog deadline reached for $id; terminating"
"$here/lambda.sh" terminate "$id" || echo "$(date -u +%FT%TZ) initial terminate call returned a failure (will keep polling)" >&2
deadline=$(( $(date +%s) + 600 ))
last_reissue=$(date +%s)
while [ "$(date +%s)" -lt "$deadline" ]; do
  listing=$("$here/lambda.sh" list)
  list_rc=$?
  if [ $list_rc -eq 0 ] && ! printf '%s\n' "$listing" | cut -f1 | grep -qx "$id"; then
    echo "$(date -u +%FT%TZ) $id is no longer listed among p3- instances (terminated)"
    exit 0
  fi
  now=$(date +%s)
  if [ $list_rc -ne 0 ]; then
    echo "$(date -u +%FT%TZ) list query failed (rc=$list_rc); will retry" >&2
  elif [ $((now - last_reissue)) -ge 30 ]; then
    "$here/lambda.sh" terminate "$id" >/dev/null 2>&1 || true
    last_reissue=$now
  fi
  sleep 15
done
echo "$(date -u +%FT%TZ) WARNING: $id still listed after 10 minutes of polling; manual check required" >&2
exit 1
