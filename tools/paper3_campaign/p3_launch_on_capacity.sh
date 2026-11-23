#!/usr/bin/env bash
# p3_launch_on_capacity.sh --commit SHA [--host h100] [--launch 1] [--interval 30] [--max-hours 72] [--regions "r1 r2"] [--driver-args "..."]
#                          [--credit-funded yes|no] [--allow-relaunch] [--dry-run]
#
# Waits for Lambda capacity of a campaign host's instance type and, the moment a region lists it, launches that host's launch ONCE and starts its driver. No terminal and no
# session is needed: start it with nohup (it keeps the Mac awake with caffeinate when there is one) and read its log.
#
#   every INTERVAL s   GET /instance-types (the key goes to curl on stdin, never on a command line); the regions that list the host's type with capacity
#   on capacity        1. a fresh price snapshot (p3_price_snapshot.sh, design 2.6: at the start of the host's campaign), 2. p3_launch.sh HOST LAUNCH --place REGION
#                      --commit SHA --price-snapshot SNAPSHOT --credit-funded yes --yes (the lease name, label, record, watchdog and ledger line are that script's), 3. the
#                      driver, `nohup p3_driver.sh RECORD`, output in rerun/paper3/driver-HOST-lN.out, and it exits 0. At most ONE launch: it never goes back to polling after
#                      a launch that succeeded, and a second watcher (or a restart) refuses while a record of that host and launch exists
#   a launch that failed (the capacity went between the listing and the call: p3_launch.sh exit 1, nothing exists) is logged and the watching goes on; a refusal (exit 2:
#                      a bad argument, no price for the type, a commit that is not on origin) ends it, because trying again would repeat it
#
# Preferred regions come first (default: the planner's places for the host, then any other region the API lists). It acts on nothing but its own launch: no terminate call,
# no instance of anyone else is listed or touched (the API listing is of instance TYPES), every name is the planner's p3- lease name.
# A launch record of the same host and launch makes it refuse to start, unless that record is terminated AND carries infrastructure_failure (a host whose GPU was not ready:
# no round ran, the launch may be made again) or --allow-relaunch is given.
#
# Files: log rerun/paper3/watch-HOST-lN.log, state $P3_LEDGER_DIR/watch-HOST-lN/ (lock, and `stop`: touch it to end the watching), driver output rerun/paper3/driver-HOST-lN.out.
# Exit: 0 launched and the driver started (or --dry-run); 1 watching ended without a launch (--max-hours, stop file); 2 usage or a refusal; 3 LAUNCHED but the driver did not start (the instance
# exists and bills: the log says the command to run). P3_WATCH_OUTDIR moves the log, the snapshots and the driver output (default rerun/paper3). Tests inject P3_WATCH_NOW (a command printing epoch seconds), P3_WATCH_SLEEP, P3_WATCH_DRIVER, P3_WATCH_DRIVER_GRACE.
set -uo pipefail
usage() { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; }
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
now() { ${P3_WATCH_NOW:-date +%s}; }
wsleep() { ${P3_WATCH_SLEEP:-sleep} "$1"; }
die() { echo "p3_launch_on_capacity.sh: $*" >&2; exit 2; }

HOST=h100 LAUNCH=1 COMMIT="" INTERVAL=30 MAX_HOURS=72 REGIONS="" DRIVER_ARGS="" CREDIT=yes RELAUNCH=0 DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --commit) COMMIT=${2:-}; shift 2 ;;
    --host) HOST=${2:-}; shift 2 ;;
    --launch) LAUNCH=${2:-}; shift 2 ;;
    --interval) INTERVAL=${2:-}; shift 2 ;;
    --max-hours) MAX_HOURS=${2:-}; shift 2 ;;
    --regions) REGIONS=${2:-}; shift 2 ;;
    --driver-args) DRIVER_ARGS=${2:-}; shift 2 ;;
    --credit-funded) CREDIT=${2:-}; shift 2 ;;
    --allow-relaunch) RELAUNCH=1; shift ;;
    --dry-run) DRY=1; shift ;;
    *) usage; exit 2 ;;
  esac
done
printf '%s' "$COMMIT" | grep -Eq '^[0-9a-f]{40}$' || die "--commit must be a full 40-character commit hash (the campaign commit)"
[[ "$HOST" =~ ^(a100|h100|gh200|b200)$ ]] || die "--host must be a Lambda host: a100 h100 gh200 b200"
[[ "$LAUNCH" =~ ^[12]$ ]] || die "--launch is 1 or 2"
[[ "$INTERVAL" =~ ^[0-9]+$ ]] && [ "$INTERVAL" -ge 15 ] || die "--interval is a whole number of seconds, at least 15 (the API allows about one request per second; the launch call one per 12 s)"
[[ "$MAX_HOURS" =~ ^[0-9]+$ ]] || die "--max-hours is a whole number"
[[ "$CREDIT" =~ ^(yes|no)$ ]] || die "--credit-funded is yes or no"
args_re='^[-A-Za-z0-9_=./ ]*$'
[[ "$DRIVER_ARGS" =~ $args_re ]] || die "--driver-args may use letters, digits, space and - _ = . / only"

plan() { if [ -n "${P3_PLAN_CMD:-}" ]; then $P3_PLAN_CMD "$@"; else python3 "$HERE/p3_plan.py" "$@"; fi; }
SPEC=$(plan launch-spec "$HOST" "$LAUNCH" --place us-south-2) || die "the planner refuses $HOST launch $LAUNCH"
ITYPE=$(printf '%s' "$SPEC" | jq -r .instance_type)
[ "$(printf '%s' "$SPEC" | jq -r .provider)" = lambda ] || die "$HOST is not a Lambda host"
if [ -z "$REGIONS" ]; then
  REGIONS=$(python3 - "$HERE" "$HOST" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import p3_plan
print(" ".join(p3_plan.plan_of(sys.argv[2]).places))
PY
) || die "cannot read the planner's places for $HOST"
fi

OUTDIR=${P3_WATCH_OUTDIR:-$REPO/rerun/paper3}
STATE=$P3_LEDGER_DIR/watch-$HOST-l$LAUNCH
LOG=$OUTDIR/watch-$HOST-l$LAUNCH.log
DRIVER=${P3_WATCH_DRIVER:-$HERE/p3_driver.sh}
DRIVER_OUT=$OUTDIR/driver-$HOST-l$LAUNCH.out
mkdir -p "$OUTDIR/snapshots" "$P3_LEDGER_DIR" || die "cannot create the output directories"
log() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }

# ---- at most one: no earlier launch of this host and launch, and no other watcher ---------------------------------------------------
if [ "$RELAUNCH" != 1 ]; then
  for record in "$P3_LEDGER_DIR"/p3-campaign-*-l"$LAUNCH"-owner-*.json; do
    [ -f "$record" ] || continue
    [ "$(jq -r .host "$record")" = "$HOST" ] && [ "$(jq -r .launch "$record")" = "$LAUNCH" ] || continue
    if [ -n "$(jq -r '.terminate_confirmed_utc // empty' "$record")" ] && [ -n "$(jq -r '.infrastructure_failure // empty' "$record")" ]; then
      log "an earlier record $(basename "$record") was an infrastructure failure before round 1 (no round ran) and is terminated: launching again is allowed"
      continue
    fi
    die "a record of $HOST launch $LAUNCH exists already ($record): it was launched; not launching a second one (--allow-relaunch overrides, deliberately)"
  done
fi
mkdir -p "$STATE" || die "cannot create $STATE"
if ! mkdir "$STATE/lock" 2>/dev/null; then
  other=$(cat "$STATE/lock/pid" 2>/dev/null || true)
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then die "another watcher (pid $other) holds $STATE/lock"; fi
  rm -rf "$STATE/lock"; mkdir "$STATE/lock" || die "cannot take $STATE/lock"
fi
echo $$ > "$STATE/lock/pid"
trap 'rm -rf "$STATE/lock"' EXIT
rm -f "$STATE/stop"
if [ -z "${P3_NO_CAFFEINATE:-}" ] && command -v caffeinate >/dev/null 2>&1; then caffeinate -i -s -w $$ >/dev/null 2>&1 & fi

END=$(( $(now) + MAX_HOURS * 3600 ))
log "=== watching Lambda for $ITYPE ($HOST launch $LAUNCH): every $INTERVAL s for up to $MAX_HOURS h; preferred regions: $REGIONS; commit $COMMIT; stop file $STATE/stop ==="
[ "$DRY" = 1 ] && { log "DRY RUN: would launch on the first capacity; nothing was polled, nothing was launched"; exit 0; }

poll=0
while :; do
  [ -e "$STATE/stop" ] && { log "stop file present: watching ended without a launch"; exit 1; }
  [ "$(now)" -ge "$END" ] && { log "--max-hours reached: watching ended without a launch"; exit 1; }
  poll=$((poll + 1))
  reply=$(lambda_call GET /instance-types 2>>"$LOG")
  listed=$(printf '%s' "$reply" | jq -r --arg t "$ITYPE" '.data[$t].regions_with_capacity_available[]?.name' 2>/dev/null)
  if ! printf '%s' "$reply" | jq -e '.data' >/dev/null 2>&1; then
    log "poll $poll: the API answer has no .data ($(printf '%s' "$reply" | head -c 200 | tr '\n' ' ')); trying again"
  elif [ -z "$listed" ]; then
    { [ "$poll" -le 3 ] || [ $(( poll % 20 )) -eq 0 ]; } && log "poll $poll: no region lists $ITYPE with capacity"
  else
    region=""
    for want in $REGIONS; do printf '%s\n' "$listed" | grep -qx "$want" && { region=$want; break; }; done
    [ -n "$region" ] || region=$(printf '%s\n' "$listed" | sort | head -n 1)
    log "poll $poll: CAPACITY for $ITYPE in $(printf '%s' "$listed" | tr '\n' ' ')-> launching in $region"
    snap="$OUTDIR/snapshots/$(date -u +%Y%m%dT%H%M%SZ)-$HOST-l$LAUNCH"
    if ! "$HERE/p3_price_snapshot.sh" --out "$snap" --yes >>"$LOG" 2>&1; then
      log "the price snapshot failed (partial files stay in $snap): not launching without one; trying again at the next poll"
    else
      out=$("$HERE/p3_launch.sh" "$HOST" "$LAUNCH" --place "$region" --commit "$COMMIT" --price-snapshot "$snap" --credit-funded "$CREDIT" --yes 2>&1); rc=$?
      printf '%s\n' "$out" | sed 's/^/    p3_launch: /' | tee -a "$LOG"
      if [ $rc -eq 0 ]; then
        record=$(printf '%s\n' "$out" | sed -n 's/^launched .*; record \(.*\)$/\1/p' | head -n 1)
        [ -f "$record" ] || { log "LAUNCHED but the record path could not be read from p3_launch.sh's output; find it in $P3_LEDGER_DIR and run: $DRIVER <record>"; exit 3; }
        log "launched: record $record"
        # shellcheck disable=SC2086
        nohup "$DRIVER" "$record" $DRIVER_ARGS >> "$DRIVER_OUT" 2>&1 &
        dpid=$!
        disown
        sleep "${P3_WATCH_DRIVER_GRACE:-5}"                                   # real seconds: has the driver survived its start?
        if kill -0 "$dpid" 2>/dev/null; then
          log "driver started: pid $dpid, output $DRIVER_OUT; the watcher is done (one launch only)"
          exit 0
        fi
        log "LAUNCHED BUT THE DRIVER DID NOT START (it ended within the first seconds; see $DRIVER_OUT). The instance exists and bills; its watchdog is armed. Run: nohup $DRIVER $record $DRIVER_ARGS >> $DRIVER_OUT 2>&1 &"
        exit 3
      elif [ $rc -eq 1 ]; then
        log "the launch call failed (capacity gone, or the API said no: nothing exists, p3_launch.sh wrote no record); watching goes on"
      else
        log "p3_launch.sh REFUSED (exit $rc): a bad argument, no price for $ITYPE in the snapshot, or a commit that is not on origin. Trying again would repeat it: stopping"
        exit 2
      fi
    fi
  fi
  wsleep "$INTERVAL"
done
