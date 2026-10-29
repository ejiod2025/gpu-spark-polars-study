# Private storage preparation observed 2026-10-28

This is a preparation plan, not an upload, retrieval receipt, complete audit or
termination permit. The root operator must provide the genuine 400-session
complete audit/replay/bundle PASS and actual paths before release mutation.
Follow `H100_PRIVATE_ARCHIVE_STORAGE.md` and `LAMBDA_SHUTDOWN_GUARD.md` throughout.

## Actual observations

- Normal authenticated `gh` 2.94.0 can access
  `ejiod2025/gpu-spark-polars-study` as ADMIN. The authenticated repository read
  reported `isPrivate:true` and `visibility:PRIVATE`.
- The fixed candidate release
  `h100-confirmatory-gclocker100-20261027-evidence-v1` returned `release not found`.
  A paginated release list was empty, and the exact tag reference returned HTTP
  404. These are observations, not reserved names or continuing permission.
- Recovery originals `prestart` and `block-01` through `block-10` have complete
  local tars, historical controls, original `.verified.json` receipts and
  extracted manifests. Independent tar SHA256 and size checks matched all eleven
  controls. Receipt identity fields and original absolute paths matched. This
  preparation did not repeat every extracted-file verification.
- The actual `final.json` declares 10,420,008,960 bytes and SHA256
  `f16f8b045b391b5882b96c0d2ffdbcf3bd751c3d63d7decc1abc1d9ac678db1a`.
  Its tar was still transferring when inventoried; the control alone is not
  final-backup verification. `completion` was absent from that inventory.
- Independent contention tar SHA256/size checks matched the pilot and main
  controls: 75,530,240 bytes /
  `87c508ae916d2c161c99a86766af97eb4a16071cb045b36e879c7ed6b78bc55a`, and
  487,321,600 bytes /
  `98be99cf74690d01053de9670669e629a166b02bd67fd51a994425006f792197`.
- The actual terminal contention replay is COMPLETE, nonsynthetic, integrity
  PASS and publication ready. Preserve its exact SHA256
  `0cad03b7ab2edc7e0d62dbcc7b84f8fa0ea51325aba41eb354b5baae0c51a2d3`.
- At initial inspection the data filesystem had about 106 GiB free. Recheck
  before staging, downloads, reassembly and extraction; reserve at least 20 GiB.

The exact contemporaneous inventory, source-file byte counts and actual hashes
are in
`rerun/gclocker100-20261027/private-storage-preparation-v1/LOCAL_INVENTORY.json`.
Its status explicitly awaits the genuine original-study PASS. It contains 84
allowlisted contention supplemental files and 40 currently observed phase
metadata files. It does not assert that remote assets or downloads exist.

## Required staging and catalog

Create a new `release-stage-v1` outside the raw harvest only after root supplies
the actual genuine complete replay/audit and completed bundle gates. Prepare
the local staging manifest before commit A if necessary; finalize the catalog
only after root supplies actual A. Refuse an existing stage and
never overwrite a file. Every stage entry must be an actual regular file with
one unique flat name and actual byte count/SHA256. Existing same-filesystem
regular files may be hardlinked into this stage to avoid duplicate disk use;
no source file may be modified through either name. Every later independent
download must use new bytes and a different inode.

The final proposed catalog must describe these exact categories:

| Asset or category | Restoration and binding |
| --- | --- |
| Actual raw bundle `evidence.tar.gz.part-*` | Keep the exact recorded part basenames and lexical order from the genuine `BUNDLE_MANIFEST.json`; restore beneath the supplied bundle-relative directory. |
| `BUNDLE_MANIFEST.json`, `CHUNKS.sha256`, `REASSEMBLE.md`, `AUDIT_COMPLETE.json` | Copy or hardlink those four exact files from that same genuine bundle directory; no reconstructed text. |
| `recovery-prestart.tar`, `recovery-block-01.tar` through `recovery-block-10.tar` | Restore to the exact historical `rerun/gclocker100-20261027/raw-backups/<phase>.tar` paths recorded by the catalog. All currently fit below 2 GiB. |
| `recovery-final.tar.part-00000` through `recovery-final.tar.part-00009` | Ten binary pieces, at most 1 GiB each, of the actual 10,420,008,960-byte final tar; stream split without altering the original. Bind each piece and joined bytes to size/SHA256 and the original control. |
| `recovery-completion.tar` or explicit ordered pieces | Decide from the actual completed transport size. Bind to its actual original control and historical receipt. |
| `recovery-original-phase-metadata.tar.gz` | Explicit file list of all thirteen original controls, original verification receipts and original extracted `BACKUP_MANIFEST.json` files; preserve their exact relative restoration paths and bytes. Add only actual final/completion records after they exist. |
| `recovery-reconstruction-lineage.tar.gz` | Exact new `RECONSTRUCTION_MANIFEST.json` plus the complete new `lineage/` file inventory, as supplied and hash-checked by root; omit the separate merged harvest. |
| `recovery-final-replay.tar.gz` | Exact completed replay manifest, audit, validation, analysis, figures, command logs and output inventories from root's new genuine replay directory. Enumerate every regular file before archiving. |
| `contention-pilot.tar`, `contention-main.tar` | Exact original unmodified tars at the paths and hashes above; neither needs splitting. |
| `contention-original-phase-metadata.tar.gz` | Both actual controls, original verification receipts and extracted `CONTENTION_BACKUP_MANIFEST.json` inventories. |
| `contention-terminal-metadata.tar.gz` | Exact allowlisted terminal `final-metadata-v1/`, full `final-handoff-v1/`, both preserved held-original directories and the small terminal checkpoint metadata from the preparation inventory. Retain original driver/remote ACKs and paths. |
| `contention-terminal-replay.tar.gz` | The five exact files in `offline-terminal-audit-v1/`, including the auditor source, terminal replay, CSVs and path-adapter record. |
| `contention-final-figures.tar.gz` | The twenty exact files in `figures-final-v1/` and the exact copied checkpoint figure/replay records if separately preserved. No draft figure directory. |
| `ASSET_CATALOG.json` | The final reviewed catalog bytes; no self-hash. Commit its bytes and record its hash in later actual retrieval receipts. |

For any supplemental tar, build exclusively from an explicit frozen file
allowlist, preserving repository-relative paths. Record every member's SHA256
and byte count, plus archive hash/size. Do not recursively tar the workspace.
Reject links, special files, paths outside the evidence base, traversal,
duplicate archive names, key/runtime/dataset files and files changed while
hashing. Empty regular files that are actual terminal evidence, such as the
held handoff lock/log, may be preserved; never operate those locks.

The optional historical failure archive is explicitly inventoried as
`historical-default-jvm-abort-evidence.tar.gz`, restoring to
`rerun/replacement-20261027/incidents/aborted-20261027T202748Z/evidence.tar.gz`.
It is 220,656,964 bytes; its actual independent hash and four original metadata
file references are in the preparation inventory. The initial preparation
inventory excluded it by default. Root's subsequent staging authorization
requires including it because the paper reports that failure. In the final
catalog, label it historical/default-JVM failure,
excluded from the recovery cohort, and preserve all four corresponding
metadata records. Never promote it to confirmatory or replay PASS evidence.

Bind the final catalog to actual campaign/profile/prestart hashes, full private
Git evidence commit, genuine bundle manifest, reconstruction manifest and
complete replay manifest. Include the separately registered contention
plan/prestart and terminal replay hashes. Every asset records its source path,
role, restoration path, bytes and SHA256. Each split original additionally
records ordered asset names, complete original hash/bytes and original control
reference/hash. Count the exact complete asset set and require fewer than or
equal to 1,000 files, each strictly below 2 GiB. Do not guess the bundle part
count, final catalog SHA, final Git commit or remote asset identifiers.

The canonical 346-file recovery prestart and 66-file contention prestart remain
in private Git at the exact final evidence commit. They are separate from the
merged raw bundle; do not add their old seals to an advanced raw payload.

Avoid a commit/catalog hash cycle. First preserve scientific evidence and its
manifests in a full private commit A. Bind the catalog's
`scientific_evidence_commit` to A, then commit the reviewed catalog in child
commit B and target the draft release at B. The catalog must not contain the
hash of its own containing commit. Later retrieval receipts record actual B,
the catalog SHA256, release identity and A. A subsequent private Git commit may
add the paper, public deployment and retrieval receipts without rewriting A,
B, the original catalog or uploaded evidence.

## Release and authenticated retrieval sequence

1. Recheck privacy and target existence. Reconcile any newly existing release
   and assets by actual tag, target commit and catalog identity. A conflicting
   asset is a stop for investigation; no deletion or `--clobber`.
2. Root must privately push the scientific-evidence commit A and the child
   catalog commit B described above. Create the candidate release as a draft
   with `--latest=false` and `--target` equal to actual B, never the default
   branch. Preserve the actual release metadata and both commit identities.
3. Upload exact cataloged stage filenames with `gh release upload`, no broad
   glob and no overwrite option. After each ambiguous failure, reconcile the
   paginated actual assets before resuming missing uploads. Capture actual
   asset IDs, uploaded state, size, digest when supplied, timestamps and stable
   repository/API/download locators. Never retain temporary redirects.
4. Create a new empty retrieval directory. If the complete download and next
   verification fit the disk reserve, use authenticated
   `gh release download <exact-tag> --repo ejiod2025/gpu-spark-polars-study
   --dir <fresh-directory>`, without `--clobber` or `--skip-existing`.
   Otherwise use exact cataloged `--pattern <flat-name>` arguments sequentially
   in that initially empty directory, checking each immediately. Compare the
   aggregate exact downloaded filename set, individual sizes and SHA256 to the
   committed trusted catalog; downloaded metadata does not become its own
   trust anchor. No previously existing file can satisfy a new download.
5. Verify downloaded bundle parts, the joined gzip hash/bytes, then safe archive
   members and every extracted raw file against the trusted bundle inventory.
   Require the downloaded `BUNDLE_MANIFEST.json` hash to equal the committed
   manifest. Use a fresh extraction tree and the supplied current helper/code.
6. Extract the canonical prestart from the exact private Git commit into a new
   location; run `research_reporting.replay.verify_archive`. Run genuine
   portable replay against the independently retrieved raw bundle and require
   its real validation, audit, analysis and figures to pass. Do not change old
   raw JSON paths or historical receipt bytes to accommodate relocation.
7. Rejoin each split original into a new file and require the original
   historical control's tar SHA256 and byte count. Sequentially use
   `research_backup.verify_archive` and `verify_directory` for all thirteen
   recovery snapshots. Extract into a fresh destination each time. Preserve
   new verification results separately from old path-bound receipts.
8. Independently download both contention assets to genuinely new files;
   compare their bytes/hash/inodes with original controls and tars. Use
   `contention_backup.verify_archive` and `verify_directory` sequentially in
   fresh trees. Reverify the raw phase set against actual terminal replay
   `snapshots` keys (`pilot` and `main`). Do not claim a relocated contention
   replay unless it was actually run; byte-identical verified tars bind the
   separately preserved actual terminal audit.
9. Only after actual checks pass, write the general
   `h100-private-evidence-release-v1` retrieval receipt and separate mandatory
   `h100-contention-private-preservation-v1` receipt. The latter must have
   `PRIVATE_DOWNLOAD_VERIFIED`, the exact terminal replay/plan/prestart hashes,
   both new retrieved tar file references, actual positive asset IDs and the
   stable private repository/tag/name locators required by the guard.
10. Publish the draft privately only after its bytes and independent retrieval
    pass. Read back published metadata and current privacy, verify the same
    actual asset identities/downloaded bytes against that state, then commit
    and privately push final receipts/instructions separately. Record actual
    immutable state if exposed; do not change repository settings or claim
    immutability from a tag alone.

No receipt is created during preparation. CLI success, metadata digests and a
bundle PASS do not replace independent authenticated download verification.

## Disk sequence and retention

Keep all original local archives and verified trees. Stage regular originals,
bundle parts and fixed metadata with hardlinks where possible; create actual
new files for binary split pieces and supplemental tar payloads. Reserve
20 GiB after accounting for every upcoming operation; stop before crossing it.

Download and verify the bundle first, retaining its cataloged downloaded bytes
and fresh replay results. Then download and verify original recovery tars one
at a time when needed for space; retain completed download hashes and exact
inventory results. Extract at most one large original tar at a time. After its
full archive/member/directory verification is recorded, its newly created
temporary extraction tree may be removed. Never remove an original tree, an
original tar, a scientific source directory or another operator's output.

Reassembling final requires about 9.71 GiB beyond downloaded pieces; its fresh
extraction requires about another final payload. Avoid retaining duplicate
temporary final extractions. Removal of any retained downloaded archive needs
coordination with root. Both independently retrieved contention tars and their
new file references must remain available through conditional retirement.

## Capability boundaries

Installed CLI help confirms draft creation with explicit target, exact-file
upload, authenticated download and draft publication. The existing research
and contention backup helpers provide hash-bound safe extraction and exact
inventory verification. The existing raw packager produces compliant bounded
raw bundle pieces, deterministic archive identity and file inventory.

`h100_private_archive.py` now supplies offline `stage` and `catalog` operations
for these exact genuine completed identities. It safely verifies a fresh local
bundle extraction, rechecks the original directories/controls/tars, splits the
final transport without changing it, builds explicit supplemental archives and
records staging provenance. `catalog` requires the actual full scientific
commit A and does not embed its own containing commit. Both commands refuse
overwrites. Seven fabricated local byte/safety tests exercise splitting, exact
allowlists, changed files, link/overwrite rejection and the synthetic gate;
the catalog test also distinguishes earlier Git source bytes from later tool
changes. These tests do not establish actual measured-study PASS.

The raw packager itself does not split original transport tars or build
supplemental archives. There is still no automatic upload/pagination/retrieval
or private preservation receipt implementation in this helper. The operator
must perform those bounded operational steps after actual A and reviewed,
privately pushed B are supplied, reusing the existing hash-bound verification
helpers. In particular, a local verification record is never a private
download verification receipt.
No SSH/API key access, VM action, source Parquet access, public publication or
retirement belongs to this storage preparation.
