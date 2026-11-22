#!/usr/bin/env bash
# p3_host_pack.sh NAME PATH...      (runs ON the host, started detached by the driver; paths are relative to ~/study)
#
# Packs the files under PATH... (a path may be a glob such as rerun/paper3-h100-l1/round-3-repeat-*; one that matches nothing is
# skipped) into ~/p3out/pack/NAME.tar.gz together with MANIFEST-NAME.sha256 (the sha256 of every packed file, paths relative to
# ~/study), and writes ~/p3out/pack/NAME.tar.gz.sha256 (the sha256 of the archive) last, atomically. The operator side copies both,
# compares the archive's hash, unpacks, and checks every file against the manifest. Run it only while no campaign process is
# running: a file that changes during the pack would be recorded with the hash of one moment and the bytes of another.
#
# Why an archive and not rsync: the same bounded, retried scp serves Lambda (ssh) and GCP (gcloud compute scp), and a copy
# that failed half-way is simply fetched again whole. P3_STUDY_DIR overrides ~/study (tests).
set -u
name=${1:-}
shift || true
case "$name" in ''|*[!A-Za-z0-9._-]*) echo "p3_host_pack.sh: bad pack name '$name'" >&2; exit 2 ;; esac
[ $# -ge 1 ] || { echo "p3_host_pack.sh: no paths" >&2; exit 2; }
for p in "$@"; do
  case "$p" in /*|*..*) echo "p3_host_pack.sh: refusing path '$p' (absolute, or with ..)" >&2; exit 2 ;; esac
done
study=${P3_STUDY_DIR:-$HOME/study}
out=$HOME/p3out/pack
mkdir -p "$out" || exit 1
sum256() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
tmp=$(mktemp -d "$out/.tmp.XXXXXX") || exit 1
trap 'rm -rf "$tmp"' EXIT
cd "$study" || { echo "p3_host_pack.sh: no $study" >&2; exit 1; }
shopt -s nullglob
: > "$tmp/files"
for p in "$@"; do
  for match in $p; do
    find "$match" -type f -print >> "$tmp/files"
  done
done
sort -u "$tmp/files" -o "$tmp/files"
if [ ! -s "$tmp/files" ]; then echo "PACK-EMPTY $name: nothing matches: $*" >&2; exit 3; fi
tr '\n' '\0' < "$tmp/files" | xargs -0 -n 200 sh -c 'if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi' _ > "$tmp/MANIFEST-$name.sha256" || exit 1
tar -cf "$tmp/$name.tar" -C "$study" -T "$tmp/files" || exit 1
tar -rf "$tmp/$name.tar" -C "$tmp" "MANIFEST-$name.sha256" || exit 1
gzip -n < "$tmp/$name.tar" > "$tmp/$name.tar.gz" || exit 1
rm -f "$tmp/$name.tar"
mv "$tmp/$name.tar.gz" "$out/$name.tar.gz" || exit 1
( cd "$out" && sum256 "$name.tar.gz" > "$name.tar.gz.sha256.tmp" && mv "$name.tar.gz.sha256.tmp" "$name.tar.gz.sha256" ) || exit 1
echo "PACK-OK $name files=$(wc -l < "$tmp/files" | tr -d ' ') bytes=$(wc -c < "$out/$name.tar.gz" | tr -d ' ') sha256=$(cut -d' ' -f1 "$out/$name.tar.gz.sha256")"
