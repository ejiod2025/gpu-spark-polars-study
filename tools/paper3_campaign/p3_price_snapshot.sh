#!/usr/bin/env bash
# p3_price_snapshot.sh --out DIR [--yes]
#
# The price snapshot of design section 2.6 ("at the lock and at the start and end of each host's campaign"): the raw provider
# responses with UTC time and sha256, read-only, nothing launched. Without --yes it prints what it would fetch.
#
#   lambda-api-instance-types.json   GET /instance-types (authenticated; the key goes to curl on stdin, never printed or stored)
#   lambda-pricing-page.html         https://lambda.com/pricing
#   gcp-general-purpose.html.gz      Google's C3 price tables; the page embeds Google's own public web-client keys, which are redacted
#   gcp-disks-image-pricing.html.gz  before compression, as in docs/paper3-evidence/prices-20261111/MANIFEST.md (hashes of both forms)
#   SNAPSHOT.md                      what was fetched when, with the hashes
#   prices.json                      snapshot_utc and, per Lambda type of the study, price_usd_per_hour and the regions with capacity
#
# The output belongs under rerun/ (not committed); the lead moves a snapshot into docs/paper3-evidence/ when it is to be archived.
# p3_launch.sh refuses a snapshot older than three hours.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=p3_ops_lib.sh
. "$HERE/p3_ops_lib.sh"
OUT="" YES=0
while [ $# -gt 0 ]; do
  case "$1" in --out) OUT=$2; shift 2 ;; --yes) YES=1; shift ;; *) echo "usage: $0 --out DIR [--yes]" >&2; exit 2 ;; esac
done
[ -n "$OUT" ] || { echo "usage: $0 --out DIR [--yes]" >&2; exit 2; }
LAMBDA_PAGE=${P3_LAMBDA_PAGE_URL:-https://lambda.com/pricing}
GCP_PAGES=("${P3_GCP_PRICE_URL:-https://cloud.google.com/products/compute/pricing/general-purpose}" "${P3_GCP_DISK_URL:-https://cloud.google.com/compute/disks-image-pricing}")
echo "would fetch: $P3_LAMBDA_API/instance-types, $LAMBDA_PAGE, ${GCP_PAGES[0]}, ${GCP_PAGES[1]} into $OUT"
if [ "$YES" != 1 ]; then echo "DRY RUN: nothing was fetched (add --yes)"; exit 0; fi
[ ! -e "$OUT/prices.json" ] || { echo "$OUT already holds a snapshot" >&2; exit 2; }
mkdir -p "$OUT" || exit 1
sha() { shasum -a 256 "$1" | awk '{print $1}'; }
manifest="$OUT/SNAPSHOT.md"
{ echo "# Price snapshot"; echo; echo "| File | Fetched (UTC) | SHA-256 as fetched | SHA-256 as stored | Source |"; echo "|---|---|---|---|---|"; } > "$manifest"
record() { echo "| $1 | $2 | $3 | $4 | $5 |" >> "$manifest"; }

started=$(p3_utc)
lambda_call GET /instance-types > "$OUT/lambda-api-instance-types.json" || { echo "Lambda API call failed" >&2; exit 1; }
jq -e '.data' "$OUT/lambda-api-instance-types.json" >/dev/null || { echo "the Lambda API answered, but without .data" >&2; exit 1; }
record lambda-api-instance-types.json "$(p3_utc)" "$(sha "$OUT/lambda-api-instance-types.json")" "same" "GET $P3_LAMBDA_API/instance-types"
curl -sSL "$LAMBDA_PAGE" > "$OUT/lambda-pricing-page.html" || { echo "Lambda pricing page fetch failed" >&2; exit 1; }
record lambda-pricing-page.html "$(p3_utc)" "$(sha "$OUT/lambda-pricing-page.html")" "same" "$LAMBDA_PAGE"
names=(gcp-general-purpose gcp-disks-image-pricing)
for i in 0 1; do
  raw="$OUT/${names[$i]}.html"
  curl -sSL "${GCP_PAGES[$i]}" > "$raw" || { echo "fetch of ${GCP_PAGES[$i]} failed" >&2; exit 1; }
  fetched=$(sha "$raw"); at=$(p3_utc)
  sed -E 's/AIza[0-9A-Za-z_-]{35}/REDACTED_GOOGLE_WEB_API_KEY/g' "$raw" > "$raw.redacted" && gzip -n -c "$raw.redacted" > "$raw.gz" && stored=$(sha "$raw.gz")
  rm -f "$raw" "$raw.redacted"
  record "${names[$i]}.html.gz" "$at" "$fetched" "$stored" "${GCP_PAGES[$i]}"
done

jq --arg t "$started" '{format:1, snapshot_utc:$t, source:"GET /instance-types (price_cents_per_hour / 100)",
  lambda: (.data | with_entries(select(.key | test("^gpu_1x_(a100_sxm4|h100_sxm5|gh200|b200_sxm6)$")))
           | map_values({price_usd_per_hour: (.instance_type.price_cents_per_hour / 100), regions_with_capacity: [.regions_with_capacity_available[].name]}))}' \
  "$OUT/lambda-api-instance-types.json" > "$OUT/prices.json" || exit 1
jq -e '(.lambda | length) == 4' "$OUT/prices.json" >/dev/null || echo "WARNING: the API did not list all four study types" >&2
(cd "$OUT" && shasum -a 256 lambda-api-instance-types.json lambda-pricing-page.html gcp-*.html.gz prices.json SNAPSHOT.md > SHA256SUMS 2>/dev/null)
echo "snapshot written to $OUT ($started)"
jq -c '.lambda | map_values(.price_usd_per_hour)' "$OUT/prices.json"
