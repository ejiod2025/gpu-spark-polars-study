#!/usr/bin/env bash
# Lambda helpers for the Paper 3 probes. Only instances whose name starts with "p3-" are ever listed, polled or terminated.
# The API key is read from ~/.config/lambda/api_key and passed to curl on stdin (never on argv, never printed).
#   lambda.sh types                         capacity of the probe classes
#   lambda.sh list                          our p3- instances: id, name, status, type, region (no IP)
#   lambda.sh launch CLASS TYPE REGION HRS  launch one instance named p3-probe-CLASS-owner-ejiod-expires-<UTC>; prints its id
#   lambda.sh wait ID                       wait for status active and SSH; writes the address to /tmp/p3probe/ID.ip
#   lambda.sh terminate ID                  terminate (only if the name starts with p3-)
set -euo pipefail
API=https://cloud.lambdalabs.com/api/v1
STATE=/tmp/p3probe
mkdir -p "$STATE"
call() { # METHOD PATH [JSON]
  local method=$1 path=$2 body=${3:-}
  if [ -n "$body" ]; then
    curl -sS -X "$method" -K <(printf 'user = "%s:"\n' "$(cat ~/.config/lambda/api_key)") -H 'Content-Type: application/json' -d "$body" "$API$path"
  else
    curl -sS -X "$method" -K <(printf 'user = "%s:"\n' "$(cat ~/.config/lambda/api_key)") "$API$path"
  fi
}
ours() { call GET /instances | jq -c '.data[] | select(.name != null and (.name|startswith("p3-")))'; }
case "${1:-}" in
  types) call GET /instance-types | jq -r '.data | to_entries[] | select(.key|test("1x_a100_sxm4|1x_gh200|1x_b200_sxm6|1x_h100_sxm5")) | "\(.key)\t\(.value.instance_type.price_cents_per_hour)\t\([.value.regions_with_capacity_available[].name]|join(","))"' ;;
  list) ours | jq -r '[.id,.name,.status,.instance_type.name,.region.name]|@tsv' ;;
  launch)
    class=$2 type=$3 region=$4 hours=$5
    expires=$(date -u -v+"${hours}"H +%Y%m%dT%H%MZ 2>/dev/null || date -u -d "+${hours} hours" +%Y%m%dT%H%MZ)
    name="p3-probe-${class}-owner-ejiod-expires-${expires}"
    body=$(jq -nc --arg r "$region" --arg t "$type" --arg n "$name" '{region_name:$r,instance_type_name:$t,ssh_key_names:["Key22"],file_system_names:[],quantity:1,name:$n}')
    out=$(call POST /instance-operations/launch "$body")
    id=$(echo "$out" | jq -r '.data.instance_ids[0] // empty')
    if [ -z "$id" ]; then echo "launch failed: $(echo "$out" | jq -c '.error // .')" >&2; exit 1; fi
    echo "$name" > "$STATE/$id.name"; echo "$id"; echo "launched $name" >&2 ;;
  wait)
    id=$2
    for _ in $(seq 1 90); do
      info=$(call GET "/instances/$id")
      status=$(echo "$info" | jq -r '.data.status // "?"'); ip=$(echo "$info" | jq -r '.data.ip // empty')
      if [ "$status" = active ] && [ -n "$ip" ]; then
        echo "$ip" > "$STATE/$id.ip"
        if ssh -i ~/.ssh/lambda_key22 -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ubuntu@"$ip" true 2>/dev/null; then echo "ssh ready"; exit 0; fi
      fi
      sleep 10
    done
    echo "timeout waiting for $id (status $status)" >&2; exit 1 ;;
  terminate)
    id=$2
    name=$(call GET "/instances/$id" | jq -r '.data.name // empty')
    case "$name" in p3-*) ;; *) echo "refusing: instance $id is not named p3-*" >&2; exit 1 ;; esac
    call POST /instance-operations/terminate "$(jq -nc --arg i "$id" '{instance_ids:[$i]}')" | jq -c '.data.terminated_instances // .error // .' ;;
  *) sed -n 2,9p "$0"; exit 2 ;;
esac
