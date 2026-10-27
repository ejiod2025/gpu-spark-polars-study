# Private durable storage after H100 completion

This is a future operations runbook. It does not assert that the recovery study,
paper, webpage, archive or cloud copy is complete. Do not execute its upload steps
until all 400 recovery sessions, final and completion snapshots, genuine replay
and complete audit have passed. Never upload synthetic fixtures as study evidence.
It adds no benchmark attempt, JVM change or new statistical decision.

The authorized repository is `ejiod2025/gpu-spark-polars-study`, branch
`H100_main_dev`, and must remain **private**. Read-only checks on October 27, 2026
found PRIVATE visibility, ADMIN permission and no existing releases. Recheck
before every mutation; these observations do not guarantee later state.

## What belongs in Git and in release assets

Keep the following small, reviewable records in the private Git history:

- The exact 346-file canonical prestart at
  `evidence-research/20261027/h100-confirmatory-gclocker100-v1`, registered
  campaign/profile identities, frozen source and operational/reporting code.
- All phase controls, original verification receipts, file manifests and
  reconstruction lineage/manifest. Keep their original bytes and paths as
  provenance; never rewrite a receipt to manufacture a successful verification.
- Final journal, audit, replay manifest, paper PDF/source and publication records
  required by `LAMBDA_SHUTDOWN_GUARD.md`. Store associated review and figure
  metadata, bundle/chunk manifests and retrieval instructions privately.
- A proposed asset catalog, then a separate actual GitHub retrieval receipt after
  verification. Neither record may contain credentials or signed redirect URLs.

Keep large payloads outside Git objects, as assets of a release in that same
private repository:

- All completed raw bundle parts and `BUNDLE_MANIFEST.json`, `CHUNKS.sha256`,
  `REASSEMBLE.md` and copied audit. The bundle retains the complete merged raw
  harvest, including logs, events, telemetry, plans and result rows.
- Every original `prestart`, `block-01` through `block-10`, `final` and
  `completion` transport tar, plus controls/receipts. Split any oversized original
  tar into bounded binary pieces without altering it; bind both each piece and
  the rejoined original tar to their SHA256 and byte count.
- A separately inventoried archive of reconstruction lineage and genuine final
  replay/analysis/figures, plus any other large publication source assets needed
  for reproduction. Use explicit file allowlists, not a tar of the workspace.

Release asset names are flat. Give supplemental or transport pieces unique
phase/category prefixes and record their intended relative restoration paths in
the catalog. Do not rename the raw bundle's recorded part paths without recording
an explicit transport mapping. Do not stage SSH/API keys, `.env`, virtual
environments, generated source Parquet or unrelated files.

The packager's `--prestart-archive` verifies and records the separate canonical
prestart identity; it does **not** add that directory, its seal, reconstruction
lineage or replay outputs to the raw gzip stream. The canonical directory is
already in private Git and must be recoverable at the recorded full commit.
Keep the seal separate from the merged harvest because operational logs advance.

## Prepare only from genuine completed evidence

Follow `H100_RECONSTRUCTION_RUNBOOK.md`, then `../research_reporting/PACKAGING.md`.
Use the exact actual recovery identities, all 13 original snapshots, a new
reconstruction directory and a new real replay directory. Package with the
separate canonical prestart and actual `AUDIT_COMPLETE.json`. Require bundle
status `PASS`, not `PASS_SYNTHETIC`, and verify all local parts, the joined gzip
SHA256 and every extracted file before cloud upload. Retain original archives.

Create an explicit catalog of every intended asset: unique name, role,
relative restoration path, byte count and SHA256. For split files also record
ordered part names and the original joined file's byte count and SHA256.
Bind catalog, bundle manifest, canonical prestart manifest, reconstruction
manifest and replay manifest hashes to the actual campaign/profile and a full
private Git commit. Avoid self-referential hashes: a catalog need not hash itself;
Git binds its bytes, and the later retrieval receipt can hash the catalog.

GitHub currently permits up to 1,000 assets per release, each **under 2 GiB**;
the packager's default 1 GiB pieces comply. Count assets and verify every staged
file size before creation. The existing packager produces raw bundle pieces;
it does not split or upload the original transport tars or supplemental archives.

## Create a private release and upload without overwrites

The following are templates for the completion operator, not commands already
executed. Replace the commit placeholder with the full final evidence commit,
verify its private push, and stage only cataloged files in a new directory.
Always set the target explicitly: the repository default branch is `main`.

```sh
STORAGE_REPO=ejiod2025/gpu-spark-polars-study
STORAGE_TAG=h100-confirmatory-gclocker100-20261027-evidence-v1
STORAGE_COMMIT=ACTUAL_FULL_FINAL_PRIVATE_EVIDENCE_COMMIT
STORAGE_STAGE="$PWD/rerun/gclocker100-20261027/release-stage-v1"
STORAGE_METADATA="$PWD/evidence-research/20261027/h100-private-storage-v1"

gh repo view "$STORAGE_REPO" \
  --json nameWithOwner,isPrivate,visibility,viewerPermission
gh release view "$STORAGE_TAG" --repo "$STORAGE_REPO"
```

Proceed only after proving PRIVATE visibility and reconciling release existence.
An existing matching release is a resume point, not permission to replace assets.
A missing release must be a genuine not-found response, not an authentication
or network error. The release notes should state private access, exact campaign,
profile, evidence commit and catalog identity, without performance claims beyond
the completed paper.

```sh
gh release create "$STORAGE_TAG" --repo "$STORAGE_REPO" \
  --draft --latest=false --target "$STORAGE_COMMIT" \
  --title 'H100 confirmatory study: private evidence' \
  --notes-file "$STORAGE_METADATA/RELEASE_NOTES.md"

# Repeat for each exact file in the reviewed catalog; no broad workspace glob.
gh release upload "$STORAGE_TAG" "$STORAGE_STAGE/EXACT_CATALOGED_ASSET" \
  --repo "$STORAGE_REPO"
```

Do not use `--clobber`: it deletes the original asset before replacement upload.
After an ambiguous timeout, list the actual assets and compare name, state, size
and digest before deciding what remains missing. Conflicting assets require
investigation; do not silently delete or replace them. Keep the release draft
while verification is pending. Publishing a release in this private repository
does not change repository visibility; recheck privacy independently.

Capture the actual release ID, tag, target commit and each asset's ID, name,
`state`, `size`, `digest`, creation/update timestamps and stable API/download
URLs. Paginate the asset listing; a partial first page cannot prove completeness.
Require the exact cataloged asset set, each state `uploaded`, matching bytes and
`sha256:` digest when supplied. A missing API digest requires independent byte
verification, never an invented digest. GitHub's upload metadata alone is not
the retrieval verification below.

## Independently download and verify

Create a new, empty retrieval directory; do not use `--skip-existing` or
`--clobber`. Use normal authenticated `gh` access; never paste a token into a
command, URL or receipt. Do not persist temporary signed download redirects.

```sh
STORAGE_RETRIEVAL="$PWD/rerun/gclocker100-20261027/release-retrieval-v1"
mkdir "$STORAGE_RETRIEVAL"
gh release download "$STORAGE_TAG" --repo "$STORAGE_REPO" \
  --dir "$STORAGE_RETRIEVAL"
```

Compare the downloaded file set, sizes and SHA256 against the trusted catalog
in the private commit. Compare the downloaded bundle manifest to that committed
manifest's exact hash, then verify every recorded part, joined compressed stream
and extracted raw file. Validate archive members before extraction: no duplicate
paths, traversal, absolute paths, links or special files; no unlisted members.
Use fresh extraction directories. Verify all original transport tars against
their controls with `research_backup.verify_archive` and verify their extracted
inventories with `verify_directory`. Rejoined original tar bytes must equal the
historical controls, not just the newly generated transfer catalog.

Historical `.verified.json` receipts bind their original absolute local paths.
`reconstruct_h100_replacement.py` intentionally checks those paths. Do not edit
old receipts to run reconstruction in a different retrieval location. Preserve
them verbatim, verify the new download with separate records, and use portable
replay on the freshly extracted merged bundle plus the canonical Git archive.
That replay creates new derived provenance while retaining all raw JSON bytes.

Verify canonical prestart from the recorded private commit using
`research_reporting.replay.verify_archive`; compare all registered identity
fields. Run actual portable replay on the independently retrieved raw bundle
and require real validation, audit, analysis and figure generation to pass.
This is artifact replay only, with no new benchmark action or VM workload.

Record the actual checks in a new private retrieval receipt. A useful proposed
schema is `h100-private-evidence-release-v1`, with status
`PRIVATE_DOWNLOAD_VERIFIED` only after all checks above pass. Include campaign
and profile hashes; repository identity and freshly observed privacy; release
ID/tag/target commit; trusted catalog and manifests' hashes; all remote asset
IDs/names/bytes/digests and independently downloaded SHA256; joined archive and
extracted inventory verification; canonical and replay verification; actual
timestamps, commands and tool versions. Mark checks individually so incomplete
work cannot appear complete. This is a new operational record, not an existing
shutdown-guard schema or a scientific-result PASS.

Once the draft's bytes and retrieval are verified, publish it privately with
`gh release edit "$STORAGE_TAG" --repo "$STORAGE_REPO" --draft=false --latest=false`.
Read back the actual published release and privacy, then repeat or confirm the
authenticated retrieval against that state and save the final receipt. Commit
and privately push that receipt and stable retrieval instructions separately;
this avoids a receipt/tag hash cycle. Record whether GitHub reports immutability;
do not claim it or enable a repository setting without an explicit decision.
Hash-bound evidence remains independently checkable when immutability is absent.

Keep the existing local originals and verified trees. A release URL, an upload
success, a Git push or a bundle PASS alone does not satisfy all publication and
retirement requirements. This storage operation does not publish the webpage or
authorize early H100 termination. Apply every gate in
`docs/H100_AUTONOMOUS_COMPLETION.md` and `LAMBDA_SHUTDOWN_GUARD.md` before retirement.

## Verified service and CLI references

- [GitHub release access and asset limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).
- [GitHub release asset metadata, upload and binary download](https://docs.github.com/en/rest/releases/assets).
- Installed `gh release create/upload/download/edit --help` was inspected.
  `gh release verify-asset` verifies signed release attestations; it is not a
  general replacement for the independent SHA256 checks above, and no signed
  attestation is presumed for this project.
