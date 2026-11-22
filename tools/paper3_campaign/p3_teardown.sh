#!/usr/bin/env bash
# p3_teardown.sh RECORD [--yes]
#
# Terminates the instance of a launch record and confirms the provider no longer lists it (up to 10 minutes). Only the instance in the
# record is touched: Lambda by its id (lambda.sh refuses any instance not named p3-*), GCP by its exact lease name and only while it is
# listed with the label project=paper3-energy. Without --yes it says what it would do. The driver does this itself at the end of a
# launch; this is for the other cases (a driver that died, a launch that never got a driver, a stopped campaign held for diagnosis).
# Writes terminate_requested_utc and terminate_confirmed_utc into the record and the ledger, and stops the record's watchdog.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
RECORD=${1:-}; shift || true
[ -f "$RECORD" ] || { echo "usage: $0 RECORD.json [--yes]" >&2; exit 2; }
YES=0; [ "${1:-}" = "--yes" ] && YES=1
echo "instance $(record_get "$RECORD" instance_id) ($(record_get "$RECORD" provider), lease $(record_get "$RECORD" lease_name)); confirmed terminated at: $(record_get "$RECORD" terminate_confirmed_utc)"
if [ "$YES" != 1 ]; then echo "DRY RUN: nothing was terminated (add --yes)"; exit 0; fi
if p3_terminate_and_confirm "$RECORD"; then
  p3_kill_watchdog "$RECORD"
  echo "confirmed terminated at $(record_get "$RECORD" terminate_confirmed_utc)"
else
  echo "NOT CONFIRMED: check the provider by hand; the watchdog stays armed" >&2
  exit 1
fi
