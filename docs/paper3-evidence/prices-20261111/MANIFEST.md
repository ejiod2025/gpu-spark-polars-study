# Price and quota evidence for docs/PAPER3_INFRA.md

Collected 2026-11-11 (UTC times below) by the p3-infra teammate. Read-only calls only; nothing was launched. Each file is stored exactly as
received (the AWS bundle and the quota/machine-type files are assembled from raw command output as stated), except the three Google pages
in `gcp-official/`, which are gzip-compressed and carry one stated redaction (see below). `SHA256SUMS` carries the hashes of the files as
stored, in `shasum -a 256` format; check with `cd docs/paper3-evidence/prices-20261111 && shasum -a 256 -c SHA256SUMS`.
No credentials, account ids, instance ids, IP addresses or tokens are in these files (scanned; the scan of the three Google pages found only
Google's own public web-client API keys, now redacted, and three Google marketing mailing-list addresses).

## Official Google prices (added 16:21-16:27 UTC; they replace the third-party pages as the GCP price source)

| File | Fetched (UTC) | SHA-256 as stored | Source and notes |
|---|---|---|---|
| `gcp-official/general-purpose.html.gz` | 2026-11-11T16:21:38Z to 16:21:44Z | `2678e87e12ea1994808b4e5b6d06b17cbc2cf18cc56067b7b2c5b74e18d37736` | `curl -sL https://cloud.google.com/products/compute/pricing/general-purpose` (Google Cloud "VM instance pricing", general-purpose page: C3 standard, high-memory and high-CPU tables including the `-metal` shapes, one table per region and per Hourly/Monthly toggle, 35.3 MB uncompressed). Uncompressed SHA-256 as fetched `9ba07c267570cf54551ea7d890e40e3170036e8c03235e9d27884bdc31b767a2`; as stored (3 occurrences of one Google web API key replaced by `REDACTED_GOOGLE_WEB_API_KEY_1`) `e8f944666ec2af61b951c46637e0685c644c0d46d213e0290daf4795d3676bfc`. |
| `gcp-official/disks-image-pricing.html.gz` | 2026-11-11T16:24:10Z to 16:24:11Z | `4ae8d11b120b5a92e3c1e8e22b8898b7744ac3dfd2689457ecb7181b927a8d6e` | `curl -sL https://cloud.google.com/compute/disks-image-pricing` ("Disk and image pricing", Hyperdisk Balanced rows per region, 3.4 MB). Uncompressed as fetched `7c739108952a425ca89ae1ec34ad9fbf447b2267c52bddc101c3804d6a115cf3`; as stored (3 occurrences of one key redacted) `0a4d28598f578aacd5f3bbf67843ee85ab0644ae30eea7343aa6887fbe57e500`. |
| `gcp-official/bare-metal-instances.html.gz` | 2026-11-11T16:24:11Z to 16:24:12Z | `f4c5b3df9512943128cd85959adbb33aa1ac38f5681e69ff1eef61e17d99dbaf` | `curl -sL https://cloud.google.com/compute/docs/instances/bare-metal-instances` (redirects to docs.cloud.google.com; page says "Last updated 2026-11-10 UTC"; C3 metal shapes, memory, supported disks, zone list). Uncompressed as fetched `b3e943a8501b85f82b73755beff904ab700036a91c1c7e71e988b08986b09887`; as stored (9 distinct keys redacted) `5250aee5298e9753cb89263f86c5ea0f60d3a2aad82a2bac33f85c29ad95a21e`. |
| `gcp-official-prices.json` | derived 2026-11-11 | see `SHA256SUMS` | Output of `extract_gcp_official_prices.py` run on the two price pages above (`python3 -I extract_gcp_official_prices.py gcp-official/general-purpose.html.gz gcp-official/disks-image-pricing.html.gz`). Rows for us-east4 and us-central1: every C3 shape (standard, highmem, highcpu; VMs and `-metal`), C4 standard/highmem 288 metal, Hyperdisk Balanced space/IOPS/throughput; hourly and monthly list price, plus the discount columns where the page shows them. The `derived` block solves C3's per-vCPU-hour and per-GiB-hour rates from two metal shapes and checks all 42 C3 rows against them. |
| `extract_gcp_official_prices.py` | written 2026-11-11 | see `SHA256SUMS` | Standard-library extractor for the embedded table data of the two price pages (the visible `<table>` shows only the default region). Run it with `python3 -I`; the pages are untrusted data. |
| `gcp-region-quotas-us-east.json` | 2026-11-11T16:27:07Z to 16:27:10Z | see `SHA256SUMS` | `gcloud compute regions describe us-east4 / us-east5 / us-east1` (project siriusmedai; CPUS, C3_CPUS, INSTANCES, SSD_TOTAL_GB, DISKS_TOTAL_GB, limit and usage; same layout as `gcp-region-quotas.json`). |

Redaction: every string matching `AIza[0-9A-Za-z_-]{35}` (Google's own public web-client keys, which every cloud.google.com page embeds in
its JavaScript, not ours) was replaced by `REDACTED_GOOGLE_WEB_API_KEY_<n>` before compression so that secret scanners do not flag the
repository; nothing else was altered, and the price data is untouched (the extractor output is identical before and after).

How the numbers were read: the pages carry a prices data block, one table per region and per Hourly/Monthly toggle, headed by the region
("Northern Virginia (us-east4)", "Iowa (us-central1)", ...). Checks that passed: (a) the three C3 metal shapes plus every C3 VM size (42 rows over the two regions) are reproduced to 2e-15
USD/hour by linear pricing of $0.03465 per vCPU-hour plus $0.003938 per GiB-hour (solved from c3-standard-192-metal and
c3-highcpu-192-metal, which differ only in memory), so bare metal carries no premium over VMs and the page prices it per shape; (b)
Monthly = 730 x Hourly in every row; (c) us-east4 and us-central1 are priced identically for every C3 and C4-metal row (for
c3-highmem-192-metal the page gives the same $12.701568 to six US regions: us-central1, us-east1, us-east4, us-east5, us-west1, us-west8);
(d) the 21 rows of the three visible C3 tables (the page's default region) equal the embedded us-central1 rows; (e) the third-party page
below, fetched earlier, lists the same $9.6772/h and $7064.34/month for c3-standard-192-metal in both regions and $14.2322/h for
c4-standard-288-metal. The page itself carries no "last updated" stamp; the fetch time above is the date of record.
Cloud Billing Catalog API: not used. Called without credentials it answers HTTP 403 ("callers without established identity ... use API
Key"); with project credentials it was refused earlier today because the API is not enabled in siriusmedai (not enabled).
`https://cloud.google.com/compute/vm-instance-pricing` (and `/compute/all-pricing`) redirect to `/products/compute/pricing`, a landing page
without price tables (fetched 16:21:22Z, 1.8 MB, not archived); the tables moved to the per-family pages, of which only
`general-purpose` is needed here.

## Earlier files (14:46-14:48 UTC)

| File | Fetched (UTC) | SHA-256 | Source and notes |
|---|---|---|---|
| `lambda-pricing-page.html` | 2026-11-11T14:46:09Z | `3a277b420f1deba0cd83020467aeb9f7df0eb4ee4534866661f42bf59eb077fa` | curl -sL https://lambda.com/pricing (raw HTML; byte-identical, same SHA-256, to the first fetch at about 13:50 UTC the same day). States 'These prices take effect November 16, 2026, at 00:00 UTC.' in each instance table. |
| `lambda-api-instance-types.json` | 2026-11-11T14:46:09Z | `a31b084545695985101a1a4658dfc4d5bda21c52e5c7080250b180332fa8b02d` | curl -s -u <key>: https://cloud.lambdalabs.com/api/v1/instance-types (raw body; no credentials in it; prices are the OLD prices, in cents per hour; the regions_with_capacity_available lists are valid only for this minute). |
| `aws-price-list-us-east-2.json` | 2026-11-11T14:46:31Z to 14:46:40Z | `d98448a68a96a5d309e528b9ad8d4cf5f21e967f76531257144bfdc08a483113` | aws pricing get-products (11 instance types, EBS gp3, EBS snapshots; us-east-2 Linux shared on-demand) |
| `aws-service-quotas-ec2.json` | 2026-11-11T14:47:03Z to 2026-11-11T14:47:42Z | `043b830c004f5b046753d08e6636f95b6a2f92b87447048abc3a074023224eba` | aws service-quotas list-service-quotas --service-code ec2 (QuotaArn dropped: it contains the account id) |
| `gcloud-compute-com-c3-standard-192-metal.html` | 2026-11-11T14:48:17Z to 2026-11-11T14:48:18Z | `431c428b82ea170f7a5e9578f1b38d5839e142c97d7578c38366618ea2456435` | curl https://gcloud-compute.com/c3-standard-192-metal.html (third party; SUPERSEDED by the official pages above, kept as the cross-check (e)) |
| `gcloud-compute-com-c4-standard-288-metal.html` | 2026-11-11T14:48:18Z to 2026-11-11T14:48:18Z | `ac386fef23501d2dc13d9db56a3a0ef9c190000ed54be4a6d99ab0cb17be4571` | curl https://gcloud-compute.com/c4-standard-288-metal.html (third party; superseded, cross-check only) |
| `gcp-region-quotas.json` | 2026-11-11T14:47:42Z to 2026-11-11T14:47:45Z | `05c00d27c0d940cd88929461ba7928b174a118ce1db5e95460925a0a9719d75d` | gcloud compute regions describe (project siriusmedai; CPUS, C3_CPUS, INSTANCES, SSD_TOTAL_GB, DISKS_TOTAL_GB; us-west1, us-central1, us-east1) |
| `gcp-metal-machine-types.csv` | 2026-11-11T14:47:45Z to 2026-11-11T14:48:17Z | `ed929e92d8647d0918d323e9687c314d4c6706462d46b264cdf76f11c4224400` | gcloud compute machine-types list --filter name~-metal$ (name,zone,guestCpus,memoryMb) |

AWS Price List publication date inside the bundle: 2026-11-10T17:38:52Z (every item carries `publicationDate`).
Parsed Lambda page rows (per GPU-hour on the 8x/4x/2x tables, per instance on the 1x table) match the table in docs/PAPER3_INFRA.md section 1.
