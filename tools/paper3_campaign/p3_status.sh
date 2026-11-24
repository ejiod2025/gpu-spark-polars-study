#!/usr/bin/env bash
# p3_status.sh RECORD
#
# One short, bounded look at a launched campaign instance, for the operator who is not watching the driver: the lease, the state of every
# stage on the host (running, or its exit code), and the QA view of the campaign root (p3_qa_view.py on the host: progress counts and gate
# outcomes only, never a timing, energy, power or cost value; design 7.3). One ssh call, retried a few times, no load on the host beyond
# reading three small JSON files. It is safe to run during a round. The driver's own log is the other view: tail -n 20 DEST/driver-*.log
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
plan() { if [ -n "${P3_PLAN_CMD:-}" ]; then $P3_PLAN_CMD "$@"; else python3 "$HERE/p3_plan.py" "$@"; fi; }
p3_log() { echo "$(date -u +%FT%TZ) $*" >&2; }
# shellcheck source=../paper3_probes/p3_stage_lib.sh
. "$P3_PROBES/p3_stage_lib.sh"
RECORD=${1:-}
[ -f "$RECORD" ] || { echo "usage: $0 RECORD.json" >&2; exit 2; }
HOST=$(record_get "$RECORD" host); LAUNCH=$(record_get "$RECORD" launch)
ROOT_REL=$(plan root "$HOST" "$LAUNCH") || exit 2
echo "lease $(record_get "$RECORD" lease_name)$([ "$(record_get "$RECORD" ordinal)" -gt 1 ] 2>/dev/null && echo " (relaunch $(record_get "$RECORD" ordinal) of $(record_get "$RECORD" relaunch_of))"), expires $(record_get "$RECORD" expires_utc), terminated at: $(record_get "$RECORD" terminate_confirmed_utc)"
p3_transport "$RECORD" || exit 1
remote='cd "$HOME/p3out" 2>/dev/null && for f in *.started; do [ -d "$f" ] || continue; s=${f%.started}; if [ -f "$s.rc" ]; then echo "stage $s rc=$(cat "$s.rc")"; else echo "stage $s running"; fi; done;
q="$HOME/study/tools/paper3_campaign/p3_qa_view.py"; if [ -f "$q" ]; then python3 "$q" "$HOME/study/'"$ROOT_REL"'"; else echo "(no QA view on the host yet)"; fi'
for attempt in 1 2 3; do
  if out=$(p3_ssh "$remote" 2>/dev/null); then printf '%s\n' "$out"; exit 0; fi
  sleep "${P3_RETRY_PAUSE:-5}"
done
echo "the host did not answer three bounded ssh calls (a stage runs on regardless; ask the provider: $(record_get "$RECORD" provider))" >&2
exit 1
