# p3_ops_lib.sh: sourced by p3_launch.sh, p3_teardown.sh and p3_driver.sh (bash 3.2 compatible; macOS /bin/bash works).
#
# The operator-side records of a launch. Nothing here talks to a cloud except lambda_call, gcp_instance_listed and
# p3_terminate_and_confirm, which are called by the three scripts above and by nothing else.
#
#   P3_LEDGER_DIR   (default ~/p3ledger)   one JSON record per lease, <lease>.json, and ledger.jsonl (one line per event)
#   The launch record is the single source of a launch's facts (provider, ids, lease, price, commit, times); p3_plan.py reads it.
#   It holds the instance's address, so it stays on the operator's machine and is not published.
#   The lease name embeds the owner id; campaign.json keeps only its hash (run_campaign.py), the record keeps it whole.
#
# Needs jq. Tests inject P3_LAMBDA_SH (the probes' lambda.sh), P3_LAMBDA_API, P3_LAMBDA_KEY_FILE.

P3_OPS_HERE=${P3_OPS_HERE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}
P3_PROBES=${P3_PROBES:-$(cd "$P3_OPS_HERE/../paper3_probes" && pwd)}
P3_LEDGER_DIR=${P3_LEDGER_DIR:-$HOME/p3ledger}
P3_LAMBDA_SH=${P3_LAMBDA_SH:-$P3_PROBES/lambda.sh}
P3_LAMBDA_API=${P3_LAMBDA_API:-https://cloud.lambdalabs.com/api/v1}
P3_LAMBDA_KEY_FILE=${P3_LAMBDA_KEY_FILE:-$HOME/.config/lambda/api_key}
# every gcloud call of these tools runs in the study's project, whatever the operator's gcloud default is (the project is shared; docs/PAPER3_INFRA.md section 7 item 11)
export CLOUDSDK_CORE_PROJECT=${P3_GCP_PROJECT:-siriusmedai}

p3_utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }

ledger_append() {      # EVENT RECORD_FILE [REASON]: one line to ledger.jsonl with the record's identifying fields (and the reason, when there is one)
  local event=$1 record=$2 reason=${3:-}
  mkdir -p "$P3_LEDGER_DIR" || return 1
  jq -c --arg event "$event" --arg at "$(p3_utc)" --arg reason "$reason" \
    '{at:$at, event:$event, host:.host, launch:.launch, provider:.provider, instance_id:.instance_id, lease_name:.lease_name} + (if $reason == "" then {} else {reason:$reason} end)' "$record" \
    >> "$P3_LEDGER_DIR/ledger.jsonl"
}

record_get() {         # RECORD KEY: the value, or empty for null
  jq -r --arg k "$2" '.[$k] // empty' "$1"
}

record_set() {         # RECORD KEY VALUE [string|number|json]: atomic in-place update (number and json take the value as JSON)
  local file=$1 key=$2 value=$3 kind=${4:-string} tmp
  tmp=$(mktemp "$file.XXXXXX") || return 1
  if [ "$kind" = number ] || [ "$kind" = json ]; then jq --arg k "$key" --argjson v "$value" '.[$k]=$v' "$file" > "$tmp"; else jq --arg k "$key" --arg v "$value" '.[$k]=$v' "$file" > "$tmp"; fi \
    && mv "$tmp" "$file" || { rm -f "$tmp"; return 1; }
}

lambda_call() {        # METHOD PATH [JSON]: the key goes to curl on stdin (-K), never on argv and never printed
  local method=$1 path=$2 body=${3:-} key
  key=$(cat "$P3_LAMBDA_KEY_FILE") || { echo "no Lambda API key at $P3_LAMBDA_KEY_FILE" >&2; return 1; }
  if [ -n "$body" ]; then
    curl -sS -X "$method" -K <(printf 'user = "%s:"\n' "$key") -H 'Content-Type: application/json' -d "$body" "$P3_LAMBDA_API$path"
  else
    curl -sS -X "$method" -K <(printf 'user = "%s:"\n' "$key") "$P3_LAMBDA_API$path"
  fi
}

gcp_instance_listed() {  # NAME: 0 if our labelled instance of that name is listed, 1 if the listing is empty, 2 if the call failed
  local found rc
  found=$(gcloud compute instances list --filter="name=$1 AND labels.project=paper3-energy" --format='value(name)' 2>/dev/null); rc=$?
  [ $rc -ne 0 ] && return 2
  [ "$found" = "$1" ]
}

p3_terminate_and_confirm() {   # RECORD: terminate the recorded instance (and only it), poll until the provider no longer lists it
  local record=$1 provider id name zone deadline rc listing gone
  provider=$(record_get "$record" provider); id=$(record_get "$record" instance_id); name=$(record_get "$record" lease_name); zone=$(record_get "$record" zone)
  case "$name" in p3-*) ;; *) echo "refusing: lease name '$name' is not p3-*" >&2; return 1 ;; esac
  record_set "$record" terminate_requested_utc "$(p3_utc)"
  ledger_append terminate-requested "$record"
  if [ "$provider" = lambda ]; then
    "$P3_LAMBDA_SH" terminate "$id" || echo "terminate call returned a failure; polling anyway" >&2
  else
    gcp_instance_listed "$name"; rc=$?
    if [ $rc -eq 1 ]; then echo "$name is not listed with our label (already gone)"; else
      [ $rc -eq 2 ] && echo "listing failed; deleting anyway by exact name" >&2
      gcloud compute instances delete "$name" --zone="$zone" --quiet || echo "delete call returned a failure; polling anyway" >&2
    fi
  fi
  # Lambda lists a terminated instance as "terminating" for many minutes (2026-11-23: over ten, for an H100 whose GPU never came up), and as "terminated" once it is done: a row with
  # status terminated is a host that no longer bills. 20 minutes by default (P3_TERMINATE_WAIT); a failed listing is still never read as "gone".
  deadline=$(( $(date +%s) + ${P3_TERMINATE_WAIT:-1200} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if [ "$provider" = lambda ]; then
      listing=$("$P3_LAMBDA_SH" list 2>/dev/null); rc=$?
      if [ $rc -eq 0 ] && ! printf '%s\n' "$listing" | awk -F'\t' -v id="$id" '$1 == id && $3 != "terminated" { found = 1 } END { exit !found }'; then gone=1; else gone=0; fi
    else
      gcp_instance_listed "$name"; rc=$?
      if [ $rc -eq 1 ]; then gone=1; else gone=0; fi
    fi
    if [ "$gone" = 1 ]; then
      record_set "$record" terminate_confirmed_utc "$(p3_utc)"
      ledger_append terminate-confirmed "$record"
      return 0
    fi
    sleep "${P3_TERMINATE_POLL:-15}"
  done
  echo "WARNING: $name still listed after ${P3_TERMINATE_WAIT:-1200} s; manual check required" >&2
  return 1
}

p3_kill_watchdog() {   # RECORD: stop the recorded watchdog (nothing left to guard once the instance is gone)
  local pid
  pid=$(record_get "$1" watchdog_pid)
  [ -n "$pid" ] && kill "$pid" 2>/dev/null
  return 0
}

p3_transport() {       # RECORD: P3_SSH, P3_SCP_FROM, P3_SCP_TO, P3_REMOTE_PREFIX for the recorded instance (Lambda needs the address in the record)
  local record=$1 provider ip lease zone
  provider=$(record_get "$record" provider); lease=$(record_get "$record" lease_name); zone=$(record_get "$record" zone)
  if [ "$provider" = lambda ]; then
    ip=$(record_get "$record" ip)
    [ -n "$ip" ] || { echo "the record has no address yet (the driver stores it once the instance is reachable)" >&2; return 1; }
    P3_SSHOPTS=(-i "${P3_LAMBDA_KEY:-$HOME/.ssh/lambda_key22}" -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
                -o ConnectTimeout=15 -o ServerAliveInterval=10 -o ServerAliveCountMax=3)
    P3_SSH=(ssh "${P3_SSHOPTS[@]}" "ubuntu@$ip"); P3_SCP_FROM=(scp "${P3_SSHOPTS[@]}"); P3_SCP_TO=(scp "${P3_SSHOPTS[@]}")
    P3_REMOTE_PREFIX="ubuntu@$ip:"
  else
    P3_GFLAGS=(--ssh-flag=-oConnectTimeout=15 --ssh-flag=-oServerAliveInterval=10 --ssh-flag=-oServerAliveCountMax=3
               --ssh-flag=-oStrictHostKeyChecking=no --ssh-flag=-oUserKnownHostsFile=/dev/null --ssh-flag=-oLogLevel=ERROR)
    P3_SFLAGS=(--scp-flag=-oConnectTimeout=15 --scp-flag=-oServerAliveInterval=10 --scp-flag=-oServerAliveCountMax=3
               --scp-flag=-oStrictHostKeyChecking=no --scp-flag=-oUserKnownHostsFile=/dev/null --scp-flag=-oLogLevel=ERROR)
    P3_SSH=(gcloud compute ssh "$lease" --zone="$zone" --quiet "${P3_GFLAGS[@]}" --command)
    P3_SCP_FROM=(gcloud compute scp --zone="$zone" --quiet --recurse "${P3_SFLAGS[@]}"); P3_SCP_TO=(gcloud compute scp --zone="$zone" --quiet "${P3_SFLAGS[@]}")
    P3_REMOTE_PREFIX="$lease:"
  fi
}
