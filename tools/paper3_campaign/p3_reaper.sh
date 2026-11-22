#!/usr/bin/env bash
# p3_reaper.sh [--terminate] [--grace-min N]
#
# The stateless last line of defence for Lambda, which has no provider-side time limit, no stop state and no spend limit: reads the
# lease out of the NAME of every p3- instance the Lambda API lists (p3-<purpose>-owner-<id>-expires-<YYYYMMDDTHHMMZ>, docs/PAPER3_INFRA.md
# section 5) and, with --terminate, terminates the ones that are past it by more than the grace (default 30 minutes). The per-launch watchdog
# (p3_launch.sh) fires at the lease itself; this one needs no pid, no record and no running driver, so it still works after a reboot of the
# machine it runs on, a crashed driver or a lost ledger. Run it from a systemd timer on the controller (docs/PAPER3_CONTROLLER.md).
#
# Without --terminate it only reports (exit 3 when something is overdue), which is what an operator machine that does not own the
# launches should do: a lease extended on the controller lives in the controller's ledger, and only that ledger can say so.
#
#   deadline = the expiry in the name, or the expires_utc of the lease's launch record in $P3_LEDGER_DIR if that is later (an extended lease;
#              a record whose termination is already confirmed does not extend anything)
#   acts on  = only instances the probes' lambda.sh lists (names starting p3-, which it also re-checks before it terminates) that carry a
#              parseable lease and are not already terminating; a p3- name without a lease is reported and never touched; instances
#              without a p3- name are never listed, let alone touched
#   a failed listing is never read as "nothing overdue" (exit 1)
#
# Environment: P3_LEDGER_DIR, P3_LAMBDA_SH (see p3_ops_lib.sh); P3_REAPER_GRACE_MIN; tests inject P3_REAPER_NOW (a command printing epoch seconds).
# Exit: 0 listed and nothing left to do (or terminations requested), 1 the listing or a termination call failed, 2 usage, 3 overdue (report only).
set -uo pipefail
usage() { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; }
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
now() { ${P3_REAPER_NOW:-date +%s}; }
stamp() { date -u +%FT%TZ; }

TERMINATE=0 GRACE_MIN=${P3_REAPER_GRACE_MIN:-30}
while [ $# -gt 0 ]; do
  case "$1" in
    --terminate) TERMINATE=1; shift ;;
    --grace-min) GRACE_MIN=${2:-}; shift 2 ;;
    *) usage; exit 2 ;;
  esac
done
case "$GRACE_MIN" in ''|*[!0-9]*) echo "p3_reaper.sh: --grace-min is a whole number of minutes" >&2; exit 2 ;; esac

LEASE_RE='^p3-[a-z0-9-]+-owner-[a-z0-9]+-expires-([0-9]{4})([0-9]{2})([0-9]{2})[Tt]([0-9]{2})([0-9]{2})[Zz]$'
epoch_of() { jq -rn --arg t "$1" '$t | fromdateiso8601' 2>/dev/null; }

listing=$("$P3_LAMBDA_SH" list 2>/dev/null) || { echo "$(stamp) the Lambda listing failed: nothing can be said about leases (retry)" >&2; exit 1; }
t_now=$(now)
overdue=0 failed=0 seen=0
while IFS=$'\t' read -r id name status itype region; do
  [ -n "$id" ] || continue
  seen=$((seen + 1))
  case "$status" in terminating|terminated) echo "$(stamp) SKIP $name ($id): $status"; continue ;; esac
  if [[ ! "$name" =~ $LEASE_RE ]]; then echo "$(stamp) NO-LEASE $name ($id, $status): a p3- name without a parseable lease; reported, not touched"; continue; fi
  deadline=$(epoch_of "${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}T${BASH_REMATCH[4]}:${BASH_REMATCH[5]}:00Z")
  [ -n "$deadline" ] || { echo "$(stamp) NO-LEASE $name ($id): the expiry in the name is not a date; not touched"; continue; }
  record="$P3_LEDGER_DIR/$name.json"
  if [ -f "$record" ] && [ -z "$(record_get "$record" terminate_confirmed_utc)" ]; then
    extended=$(epoch_of "$(record_get "$record" expires_utc)")
    [ -n "$extended" ] && [ "$extended" -gt "$deadline" ] && deadline=$extended
  fi
  late=$(( t_now - deadline ))
  if [ "$late" -le $(( GRACE_MIN * 60 )) ]; then
    echo "$(stamp) OK $name ($id, $status): lease ends in $(( (deadline - t_now) / 60 )) min (grace ${GRACE_MIN} min after it)"
    continue
  fi
  overdue=$((overdue + 1))
  if [ "$TERMINATE" != 1 ]; then echo "$(stamp) OVERDUE $name ($id, $status): $(( late / 60 )) min past its lease; would terminate (--terminate)"; continue; fi
  echo "$(stamp) OVERDUE $name ($id, $status): $(( late / 60 )) min past its lease; terminating"
  if "$P3_LAMBDA_SH" terminate "$id"; then
    mkdir -p "$P3_LEDGER_DIR" && jq -nc --arg at "$(p3_utc)" --arg id "$id" --arg name "$name" '{at:$at, event:"reaper-terminate-requested", instance_id:$id, lease_name:$name}' >> "$P3_LEDGER_DIR/ledger.jsonl"
  else
    echo "$(stamp) the terminate call for $id failed; the next pass tries again" >&2; failed=$((failed + 1))
  fi
done <<< "$listing"
echo "$(stamp) reaper pass: $seen p3- instance(s) listed, $overdue overdue$([ "$TERMINATE" = 1 ] || echo ' (report only)')"
[ "$failed" -eq 0 ] || exit 1
[ "$TERMINATE" = 1 ] || [ "$overdue" -eq 0 ] || exit 3
exit 0
