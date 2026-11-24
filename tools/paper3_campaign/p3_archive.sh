#!/usr/bin/env bash
# p3_archive.sh HOST LAUNCH [--dir BASE] [--yes]       LAUNCH is 1 or 2, or 1-r2 / 2-r2 for the directory of a relaunch (launch1-r2)
#
# The second copy of a launch (docs/PAPER3_RUNBOOK.md section 3 item 6): the launch directory rerun/paper3/HOST/launchN on THIS machine goes to
# gs://siriusmedai-gpu-benchmark-archive/gpu-spark-polars-study/paper3-campaign/HOST/launchN, the bucket Paper 1's results are in. It runs on the operator's
# machine with the operator's own credentials; no hosts and no controller hold a Google identity. Without --yes it says what it would copy and does nothing.
#
#   before   what is on disk must verify: LAUNCH-SHA256SUMS when the launch is finished, else every MANIFEST-*.sha256 the driver wrote for the packs it
#            has copied (so a running launch can be archived round by round, and the upload never carries a file that the driver itself has not checked);
#   upload   `gcloud storage rsync --recursive`, never --delete-unmatched-destination-objects: nothing in the bucket is removed or touched outside the
#            launch's own prefix. The destination must lie under a `paper3-campaign/` prefix: P3_ARCHIVE_URL cannot point at Paper 1's objects;
#   after    the bucket's object count and total bytes under the prefix must equal the local files'. A mismatch is a failure (exit 1), not a warning.
#
# Not uploaded: .incoming/ (the duplicate archives the driver unpacked) and .driver.pid. The copy is blind data: nothing here opens a result file, and the
# blinding rule (design 7.3) applies to anyone who reads it. Environment: P3_ARCHIVE_URL, P3_GCP_PROJECT (default siriusmedai, as the other tools).
# Exit: 0 uploaded and verified (or dry run), 1 the upload or its verification failed, 2 usage or a refusal.
set -uo pipefail
usage() { sed -n '2,/^set -uo pipefail/p' "$0" | sed '$d'; }
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
export CLOUDSDK_CORE_PROJECT=${P3_GCP_PROJECT:-siriusmedai}
die() { echo "p3_archive.sh: $*" >&2; exit 2; }

[ $# -ge 2 ] || { usage; exit 2; }
HOST=$1 LAUNCH=$2; shift 2
BASE=$REPO YES=0
while [ $# -gt 0 ]; do
  case "$1" in --dir) BASE=${2:-}; shift 2 ;; --yes) YES=1; shift ;; *) die "unknown option $1" ;; esac
done
[[ "$HOST" =~ ^(a100|h100|gh200|b200|c-gcp|c-vm)$ ]] && [[ "$LAUNCH" =~ ^[12](-r2)?$ ]] || die "HOST is a100 h100 gh200 b200 c-gcp c-vm, LAUNCH is 1 or 2 (1-r2 or 2-r2 for the directory of a relaunch)"
URL=${P3_ARCHIVE_URL:-gs://siriusmedai-gpu-benchmark-archive/gpu-spark-polars-study/paper3-campaign}
[[ "$URL" =~ ^gs://[a-z0-9._-]+/([A-Za-z0-9._/-]+/)?paper3-campaign$ ]] || die "the archive prefix must be gs://BUCKET/.../paper3-campaign (got '$URL'): nothing outside it is ever written"
SRC=$BASE/rerun/paper3/$HOST/launch$LAUNCH
DEST=$URL/$HOST/launch$LAUNCH
[ -d "$SRC" ] || die "no launch directory $SRC (for a Lambda launch: p3_ctl.sh pull first)"

# ---- what is on disk must verify ---------------------------------------------------------------------------------------------
if [ -f "$SRC/LAUNCH-SHA256SUMS" ]; then
  (cd "$SRC" && shasum -a 256 -c LAUNCH-SHA256SUMS >/dev/null 2>&1) || die "$SRC does not match its LAUNCH-SHA256SUMS: nothing is uploaded"
  basis="LAUNCH-SHA256SUMS (finished launch)"
else
  manifests=$(cd "$SRC" && ls MANIFEST-*.sha256 2>/dev/null)
  [ -n "$manifests" ] || die "$SRC has neither LAUNCH-SHA256SUMS nor any MANIFEST-*.sha256: the driver has not verified anything there yet"
  for m in $manifests; do (cd "$SRC" && shasum -a 256 -c "$m" >/dev/null 2>&1) || die "$SRC does not match $m: nothing is uploaded"; done
  basis="$(echo "$manifests" | wc -l | tr -d ' ') pack manifest(s) (launch still running or not finished)"
fi

STATS_PY='
import os, sys
n = total = 0
for base, dirs, files in os.walk(sys.argv[1]):
    dirs[:] = [d for d in dirs if d != ".incoming"]
    for name in files:
        if name == ".driver.pid":
            continue
        n += 1
        total += os.path.getsize(os.path.join(base, name))
print(n, total)
'
read -r want_n want_bytes < <(python3 -c "$STATS_PY" "$SRC")
EXCLUDE='(^|/)\.incoming/|(^|/)\.driver\.pid$'
echo "archive $HOST launch $LAUNCH: $want_n file(s), $want_bytes bytes, verified against $basis"
echo "  from $SRC"
echo "  to   $DEST"
if [ "$YES" != 1 ]; then echo "DRY RUN: nothing was uploaded (add --yes)"; exit 0; fi

gcloud storage rsync --recursive --exclude="$EXCLUDE" "$SRC" "$DEST" || { echo "p3_archive.sh: the upload failed; run it again (rsync resumes)" >&2; exit 1; }

# ---- after: the bucket holds what the disk holds -----------------------------------------------------------------------------------
listing=$(gcloud storage ls --recursive --long "$DEST/**" 2>/dev/null) || { echo "p3_archive.sh: could not list $DEST to verify the upload" >&2; exit 1; }
got_n=$(printf '%s\n' "$listing" | awk '$3 ~ /^gs:\/\// && $1 ~ /^[0-9]+$/ {n++} END {print n+0}')
got_bytes=$(printf '%s\n' "$listing" | awk '$3 ~ /^gs:\/\// && $1 ~ /^[0-9]+$/ {s+=$1} END {printf "%d", s+0}')
if [ "$got_n" != "$want_n" ] || [ "$got_bytes" != "$want_bytes" ]; then
  echo "p3_archive.sh: the bucket holds $got_n object(s), $got_bytes bytes under $DEST; the disk holds $want_n file(s), $want_bytes bytes: NOT verified" >&2
  exit 1
fi
echo "uploaded and verified: $got_n object(s), $got_bytes bytes under $DEST"
