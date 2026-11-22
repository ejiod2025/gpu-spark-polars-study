#!/usr/bin/env bash
# p3_open_log.sh RECORD LOGNAME [--tail N] [--dest DIR]
#
# The one sanctioned way to read an archived stage log before the campaign ends (design 7.3: nobody looks at a timing until every round is done). The logs of
# the bring-up and of the rounds are copied off the host and archived unread, because they can carry wall times (`[PASS] run.baseline: ... in 123s`). The
# exception is a stage that FAILED and held the driver (exit 20): reading its log is how the failure is diagnosed. The driver lists such a log in
# DEST/.held-logs when it holds; this script opens exactly those, refuses every other file, and records each opening in DEST/log-openings.log and in
# the ledger (event held-log-opened), so that "outcome data seen" for the next section 12 amendment (design 7.4) is a fact in the records, not a memory.
#
#   RECORD    the launch record (~/p3ledger/<lease>.json)
#   LOGNAME   a file name from .held-logs, for example bringup-a1.log or campaign-r2-a1.log (no path)
#   --tail N  only the last N lines (still logged as an opening)
#   --dest    the launch directory (default: the driver's, rerun/paper3/HOST/launchN under the repository)
#
# Exit: 0 opened, 2 usage or refused (not held, not a log name, no such file).
set -uo pipefail
usage() { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; }
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
die() { echo "p3_open_log.sh: $*" >&2; exit 2; }

RECORD=${1:-}; NAME=${2:-}
[ -f "$RECORD" ] && [ -n "$NAME" ] || { usage; exit 2; }
shift 2
TAIL="" DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tail) TAIL=${2:-}; shift 2 ;;
    --dest) DEST=${2:-}; shift 2 ;;
    *) die "unknown option $1" ;;
  esac
done
[ -z "$TAIL" ] || [[ "$TAIL" =~ ^[0-9]+$ ]] || die "--tail takes a number of lines"
[[ "$NAME" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*\.log$ ]] || die "'$NAME' is not a log file name (a bare name such as bringup-a1.log, no path)"
HOST=$(record_get "$RECORD" host); LAUNCH=$(record_get "$RECORD" launch); LEASE=$(record_get "$RECORD" lease_name)
[ -n "$HOST" ] && [ -n "$LAUNCH" ] && [ -n "$LEASE" ] || die "$RECORD is not a launch record"
DEST=${DEST:-$REPO/rerun/paper3/$HOST/launch$LAUNCH}
if [ ! -f "$DEST/.held-logs" ] || ! grep -qxF -- "$NAME" "$DEST/.held-logs"; then
  held=$( [ -f "$DEST/.held-logs" ] && sort -u "$DEST/.held-logs" | tr '\n' ' ' )
  die "$NAME is not the log of a stage that failed and held the driver: it stays closed until the campaign ends (design 7.3). Held logs of this launch: ${held:-none}"
fi
[ -f "$DEST/$NAME" ] || die "$NAME is listed as held but is not in $DEST (the copy from the host failed; it is on the host under ~/p3out)"

who=${USER:-$(id -un)}
echo "$(p3_utc) OPENED $NAME by $who: diagnosis of a held stage (design 7.3 exception); write what was seen into the next section 12 amendment (design 7.4)" >> "$DEST/log-openings.log"
mkdir -p "$P3_LEDGER_DIR" && jq -nc --arg at "$(p3_utc)" --arg lease "$LEASE" --arg log "$NAME" --arg by "$who" \
  '{at:$at, event:"held-log-opened", lease_name:$lease, log:$log, by:$by}' >> "$P3_LEDGER_DIR/ledger.jsonl"
echo "p3_open_log.sh: opening $NAME (logged in $DEST/log-openings.log and the ledger)" >&2
if [ -n "$TAIL" ]; then tail -n "$TAIL" "$DEST/$NAME"; else cat "$DEST/$NAME"; fi
