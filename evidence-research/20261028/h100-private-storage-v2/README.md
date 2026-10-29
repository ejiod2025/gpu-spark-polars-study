# Private H100 lossless transport v2

The v2 catalog changes transport encoding only. Scientific commit A remains
`9faf7648e04233de3d3ca44cfe96b2cc15048ade`. The exact v1 catalog and commit
`9315acbe1cf1f0dd47ac82767b3eb9674c3cdfa6`, its source-provenance correction,
private superseded/incomplete draft and seven uploaded assets remain preserved.
No original tar, historical receipt, measured observation or scientific source
is modified or pooled with another cohort.

`ASSET_CATALOG.json` has SHA256
`8b0a8e213b01ba89eaa3db827abd802d11e72b99c66536b84899ad213b050226`
and 47,796 bytes. It defines 32 payload assets plus the catalog, 33 total assets
and 2,829,856,326 wire bytes including the catalog. The actual catalog commit B2
must be reviewed and privately pushed by root before creating the separate tag
`h100-confirmatory-gclocker100-20261027-evidence-v2`. B2 is deliberately absent
from the catalog itself; later actual retrieval receipts bind it.

All thirteen recovery original tars use deterministic gzip envelopes, with
filename/header timestamp omitted, compression level 6 and software versions
recorded in `TRANSPORT_ENVELOPES.json`. Each asset has its actual wire bytes and
SHA256, transport encoding, original decoded tar bytes/SHA256, exact historical
restoration path and original control/receipt references. Total original
recovery tar bytes 20,894,955,520 become 1,349,455,441 wire bytes. Staging separately
decoded every gzip stream and matched the original hashes and byte counts.
Both contention tar assets keep their exact original tar bytes. The complete
raw bundle and all eight supplemental archives retain their exact v1 bytes.

Retrieval must independently download every exact cataloged asset into new
files. Check compressed wire SHA256/size first. For each recovery transport,
decode its one gzip envelope into a new tar file outside the download directory;
require the decoded file's original historical control SHA256 and byte count.
Then run the unchanged `research_backup.verify_archive` and `verify_directory`
in a fresh safe extraction directory. Gzip integrity alone is insufficient.
For contention, use the exact downloaded tar bytes and the existing
`contention_backup` verifiers directly. Preserve both new contention tar files
and their actual positive release asset IDs through conditional retirement.

The existing private release transfer and retrieval verification helpers are
configured through `operations/h100_private_release_v2.py` using actual reviewed
B2 and catalog SHA256 arguments. They retain the same privacy, no-clobber,
pagination, independent inode, safe archive-member, full file-inventory,
canonical Git prestart and genuine portable replay gates. Only newly created
verified temporary extraction trees may be removed, with cleanup recorded.
Keep a 20 GiB reserve and every original tar/extracted tree. All thirteen newly
decoded recovery tar files can be retained with this transport's disk budget.

No private cloud download verification, publication or retirement is asserted
by this local catalog. The incomplete v1 release is never promoted to verified
preservation. After actual v2 downloads, decoded-original verifications and
fresh genuine portable replay pass, create real preservation receipts, publish
the private draft, and read back privacy, release/tag target and all assets.
