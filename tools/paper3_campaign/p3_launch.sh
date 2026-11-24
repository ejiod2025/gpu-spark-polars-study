#!/usr/bin/env bash
# p3_launch.sh HOST LAUNCH --place PLACE --commit SHA --price-snapshot DIR [--type T] [--now ISO] [--credit-funded yes|no] [--relaunch-of RECORD] [--old-dest DIR] [--yes]
#
# Launches ONE campaign instance (HOST: a100 h100 gh200 b200 c-gcp c-vm; LAUNCH: 1 or 2) under the lease convention, records it, and
# arms the wall-clock watchdog. Without --yes it prints the request it would send and exits: no cloud call is made.
#
#   PLACE            Lambda region (us-south-2 ...) or GCP zone (us-east4-c ...); p3_plan.py says which are in the frozen price table
#   --commit SHA     the campaign commit the host will check out: a descendant of the lock commit, pushed to origin
#   --price-snapshot DIR   output of p3_price_snapshot.sh, taken at most 3 hours ago: the Lambda price at launch and the snapshot time
#   --credit-funded  whether the provider bills this instance against credits: Lambda defaults to yes (the funding statement), GCP has no
#                    default (the author says)
#   --relaunch-of RECORD   the replacement of a launch that stopped by rule or was lost (amendment A1 item 2): RECORD is the old launch's record (a path, or its lease name in
#                    the ledger). The new instance gets lease token lNr2 (p3-campaign-cvm-l1r2-owner-...), a record with ordinal 2 and relaunch_of = the old lease, and its own
#                    local directory rerun/paper3/HOST/launchN-r2 (the driver derives it from the record); it runs the same campaign root and launch number on the new host, so the
#                    analysis finds it by its campaign.json and labels it HOST-lNr2. Refused (before anything is launched, also in the dry run) unless the old record has
#                    terminate_confirmed_utc and its directory holds a LAUNCH-SHA256SUMS that verifies, and refused when a relaunch of this (HOST, LAUNCH) exists already:
#                    a launch is relaunched once. --old-dest names the old directory when it is not rerun/paper3/HOST/launchN.
#
# What it does with --yes, in this order, so that an instance that exists is never untracked: the provider call; the launch record
# ($P3_LEDGER_DIR/<lease>.json); the watchdog (Lambda: tools/paper3_probes/lambda_watchdog.sh; GCP: p3_gcp_watchdog.sh), armed for the
# lease (a wall-clock deadline, so a sleeping Mac fires it at the first wake; it is a Mac process, so it cannot act while the Mac is off:
# on GCP the provider enforces --max-run-duration as well, on Lambda nothing else does); the ledger line. The driver (p3_driver.sh) takes
# the record from here. Terminating is p3_teardown.sh, or the driver at the end of the launch.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
plan() { if [ -n "${P3_PLAN_CMD:-}" ]; then $P3_PLAN_CMD "$@"; else python3 "$HERE/p3_plan.py" "$@"; fi; }
die() { echo "p3_launch.sh: $*" >&2; exit 2; }

[ $# -ge 2 ] || { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; exit 2; }
HOST=$1 LAUNCH=$2; shift 2
PLACE="" COMMIT="" SNAP="" TYPE="" NOW="" CREDIT="" YES=0 RELAUNCH_OF="" OLD_DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --place) PLACE=$2; shift 2 ;;
    --commit) COMMIT=$2; shift 2 ;;
    --price-snapshot) SNAP=$2; shift 2 ;;
    --type) TYPE=$2; shift 2 ;;
    --now) NOW=$2; shift 2 ;;
    --credit-funded) CREDIT=$2; shift 2 ;;
    --relaunch-of) RELAUNCH_OF=$2; shift 2 ;;
    --old-dest) OLD_DEST=$2; shift 2 ;;
    --yes) YES=1; shift ;;
    *) die "unknown option $1" ;;
  esac
done
[ -n "$PLACE" ] || die "--place is required"
printf '%s' "$COMMIT" | grep -Eq '^[0-9a-f]{40}$' || die "--commit must be a full 40-character commit hash"
case "$CREDIT" in ''|yes|no) ;; *) die "--credit-funded is yes or no" ;; esac

args=(launch-spec "$HOST" "$LAUNCH" --place "$PLACE")
[ -n "$TYPE" ] && args+=(--type "$TYPE")
[ -n "$NOW" ] && args+=(--now "$NOW")
ORDINAL=1 RELAUNCH_LEASE=""
[ -z "$OLD_DEST" ] || [ -n "$RELAUNCH_OF" ] || die "--old-dest belongs to --relaunch-of"
if [ -n "$RELAUNCH_OF" ]; then
  # before anything else, so that a dry run shows the refusal too: the old instance is confirmed gone, its data is copied and intact, and this is the first relaunch
  REL=$(plan check-relaunch "$HOST" "$LAUNCH" --old "$RELAUNCH_OF" --ledger "$P3_LEDGER_DIR" ${OLD_DEST:+--old-dest "$OLD_DEST"}) || exit 2
  ORDINAL=$(echo "$REL" | jq -r .ordinal); RELAUNCH_LEASE=$(echo "$REL" | jq -r .relaunch_of)
  args+=(--ordinal "$ORDINAL")
  echo "relaunch $ORDINAL of $RELAUNCH_LEASE: its copy verifies ($(echo "$REL" | jq -r .old_files_verified) files in $(echo "$REL" | jq -r .old_dest)); the new launch's data goes to $(echo "$REL" | jq -r .new_dest); the stopped launch is reported, never pooled"
elif [ -d "$P3_LEDGER_DIR" ]; then
  # a plain launch of a (host, launch) that has a record already: the driver writes into rerun/paper3/HOST/launchN, which is the old launch's directory unless it is moved or --dest is used
  OTHERS=$(for f in "$P3_LEDGER_DIR"/p3-*.json; do [ -f "$f" ] && jq -r --arg h "$HOST" --arg l "$LAUNCH" 'select(.host == $h and (.launch|tostring) == $l) | .lease_name' "$f" 2>/dev/null; done)
  [ -z "$OTHERS" ] || echo "NOTE: the ledger already holds a record of $HOST launch $LAUNCH ($(echo "$OTHERS" | tr '\n' ' ')). If that launch ran rounds and this replaces it, use --relaunch-of (own lease token, own directory, one relaunch); otherwise ignore this note."
fi
SPEC=$(plan "${args[@]}") || exit 2
PROVIDER=$(echo "$SPEC" | jq -r .provider)
LEASE=$(echo "$SPEC" | jq -r .lease_name)
ITYPE=$(echo "$SPEC" | jq -r .instance_type)
SECONDS_TO_EXPIRY=$(echo "$SPEC" | jq -r '.lease_hours * 3600 | floor')
[ -n "$CREDIT" ] || CREDIT=$(echo "$SPEC" | jq -r '.credit_funded_default // empty')

echo "request for $HOST launch $LAUNCH ($PROVIDER, $ITYPE, $PLACE), lease $LEASE, expires $(echo "$SPEC" | jq -r .expires_utc):"
if [ "$PROVIDER" = lambda ]; then echo "$SPEC" | jq '{api_path, body}'; else echo "$SPEC" | jq -r '.command | map(@sh) | join(" ")'; fi
if [ "$YES" != 1 ]; then echo "DRY RUN: nothing was launched (add --yes)"; exit 0; fi

[ -n "$CREDIT" ] || die "--credit-funded yes|no is required for $HOST: whether the provider bills this instance against credits is the author's to say"
[ -n "$SNAP" ] && [ -f "$SNAP/prices.json" ] || die "--price-snapshot DIR with a prices.json (p3_price_snapshot.sh) is required"
SNAP_UTC=$(jq -r .snapshot_utc "$SNAP/prices.json")
SNAP_AGE=$(python3 - "$SNAP_UTC" <<'PY'
import sys
from datetime import datetime, timezone
print(int((datetime.now(timezone.utc) - datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))).total_seconds()))
PY
)
[ "${SNAP_AGE:-999999}" -le "${P3_SNAPSHOT_MAX_AGE_S:-10800}" ] || die "the price snapshot is $SNAP_AGE s old (more than ${P3_SNAPSHOT_MAX_AGE_S:-10800}); take a new one"
if [ "$PROVIDER" = lambda ]; then
  PRICE=$(jq -r --arg t "$ITYPE" '.lambda[$t].price_usd_per_hour // empty' "$SNAP/prices.json")
  [ -n "$PRICE" ] || die "the snapshot has no Lambda price for $ITYPE"
else
  PRICE=$(echo "$SPEC" | jq -r .price_usd_h)
fi
if [ -z "${P3_SKIP_PUSH_CHECK:-}" ]; then
  git -C "$REPO" fetch --quiet origin || die "git fetch origin failed: the host clones the commit from origin"
  git -C "$REPO" branch -r --contains "$COMMIT" | grep -q . || die "commit $COMMIT is not on any origin branch: push it first"
  LOCK_COMMIT=$(python3 -c "import sys; sys.path.insert(0, '$HERE'); import p3_plan; print(p3_plan.LOCK['commit'])")
  git -C "$REPO" merge-base --is-ancestor "$LOCK_COMMIT" "$COMMIT" || die "commit $COMMIT is not a descendant of the lock commit $LOCK_COMMIT"
fi

mkdir -p "$P3_LEDGER_DIR" || die "cannot create $P3_LEDGER_DIR"
RECORD="$P3_LEDGER_DIR/$LEASE.json"
[ ! -e "$RECORD" ] || die "$RECORD exists: this lease was launched already"
ID="" ZONE="" REGION="" CREATED="" DISK_ACTUAL=""
if [ "$PROVIDER" = lambda ]; then
  REGION=$PLACE
  OUT=$(lambda_call POST "$(echo "$SPEC" | jq -r .api_path)" "$(echo "$SPEC" | jq -c .body)")
  ID=$(echo "$OUT" | jq -r '.data.instance_ids[0] // empty')
  if [ -z "$ID" ]; then echo "launch failed: $(echo "$OUT" | jq -c '.error // .' 2>/dev/null)" >&2; exit 1; fi
else
  ZONE=$PLACE
  create_cmd=$(echo "$SPEC" | jq -r '.command | @sh')
  eval "$create_cmd" || { echo "launch failed (gcloud returned an error; nothing to terminate unless it is listed)" >&2; exit 1; }
  ID=$(gcloud compute instances describe "$LEASE" --zone="$ZONE" --format='value(id)' 2>/dev/null) || ID=""
  CREATED=$(gcloud compute instances describe "$LEASE" --zone="$ZONE" --format='value(creationTimestamp)' 2>/dev/null) || CREATED=""
  [ -n "$ID" ] || ID=$LEASE
  # what the provider actually provisioned for the boot disk (the design records the storage type; the request is not proof)
  DISK_ACTUAL=$(gcloud compute disks describe "$LEASE" --zone="$ZONE" --format=json 2>/dev/null \
    | jq -c '{size_gb: (.sizeGb|tonumber), type: (.type|split("/")|last), provisioned_iops: .provisionedIops, provisioned_throughput: .provisionedThroughput}' 2>/dev/null) || DISK_ACTUAL=""
fi
LAUNCH_UTC=$(p3_utc)
jq -n --arg host "$HOST" --argjson launch "$LAUNCH" --arg provider "$PROVIDER" --arg type "$ITYPE" --arg region "$REGION" --arg zone "$ZONE" \
  --arg id "$ID" --arg lease "$LEASE" --arg launch_utc "$LAUNCH_UTC" --arg created "$CREATED" --arg expires "$(echo "$SPEC" | jq -r .expires_utc)" \
  --arg price "$PRICE" --arg snap "$SNAP_UTC" --arg credit "$CREDIT" --arg commit "$COMMIT" --argjson hours "$(echo "$SPEC" | jq .lease_hours)" \
  --argjson disk_requested "$(echo "$SPEC" | jq -c '.boot_disk // null')" --argjson disk_actual "${DISK_ACTUAL:-null}" \
  --arg termination_action "$(echo "$SPEC" | jq -r '.termination_action // empty')" \
  --argjson access "$(echo "$SPEC" | jq -c '.access // null')" \
  --argjson ordinal "$ORDINAL" --arg relaunch_of "$RELAUNCH_LEASE" \
  '{format:1, host:$host, launch:$launch, ordinal:$ordinal, relaunch_of:(if $relaunch_of=="" then null else $relaunch_of end), provider:$provider, instance_type:$type, region:(if $region=="" then null else $region end),
    zone:(if $zone=="" then null else $zone end), instance_id:$id, lease_name:$lease, launch_utc:$launch_utc,
    launch_utc_basis:"operator clock when the provider call returned", provider_created:(if $created=="" then null else $created end),
    expires_utc:$expires, lease_hours:$hours, price_usd_h:($price|tonumber), price_snapshot_utc:$snap, credit_funded:$credit,
    boot_disk_requested:$disk_requested, boot_disk_actual:$disk_actual, termination_action:(if $termination_action=="" then null else $termination_action end), access:$access, campaign_commit:$commit, ip:null, watchdog_pid:null, terminate_requested_utc:null, terminate_confirmed_utc:null}' > "$RECORD" \
  || { echo "INSTANCE $ID IS RUNNING but the record could not be written; terminate it by hand (p3_teardown.sh needs the record)" >&2; exit 1; }
echo "launched $LEASE (id $ID); record $RECORD"

if [ "$PROVIDER" = lambda ]; then WATCHDOG=${P3_LAMBDA_WATCHDOG:-$P3_PROBES/lambda_watchdog.sh}; WARGS=("$ID" "$SECONDS_TO_EXPIRY")
else WATCHDOG=${P3_GCP_WATCHDOG:-$HERE/p3_gcp_watchdog.sh}; WARGS=("$LEASE" "$ZONE" "$SECONDS_TO_EXPIRY"); fi
# a host that the provider STOPs at its limit (P3_GCP_TERMINATION_ACTION=STOP, docs/PAPER3_CONTROLLER.md) keeps its disk: the watchdog stops it too and never deletes it
WD_ACTION=$(echo "$SPEC" | jq -r '(.termination_action // "DELETE") | ascii_downcase')
P3_GCP_WATCHDOG_ACTION=$WD_ACTION nohup "$WATCHDOG" "${WARGS[@]}" >> "$P3_LEDGER_DIR/watchdog-$LEASE.log" 2>&1 &
WPID=$!
disown
record_set "$RECORD" watchdog_pid "$WPID" number
if [ -z "${P3_NO_CAFFEINATE:-}" ] && command -v caffeinate >/dev/null 2>&1; then caffeinate -i -s -w "$WPID" >/dev/null 2>&1 & disown; fi
ledger_append launch "$RECORD" "${RELAUNCH_LEASE:+relaunch $ORDINAL of $RELAUNCH_LEASE}"
echo "watchdog pid $WPID armed for $SECONDS_TO_EXPIRY s of wall-clock time (log $P3_LEDGER_DIR/watchdog-$LEASE.log)"
echo "next: p3_driver.sh $RECORD"
