#!/usr/bin/env python3
"""Verify exact private checkpoint bytes and tracked membership, without rewrites."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

sys.dont_write_bytecode=True
ROOT=Path('/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study')
CHECKPOINT=ROOT/'evidence-research/20261028/gclocker100-block09-raw-checkpoint'
manifest_path=CHECKPOINT/'CHECKPOINT_MANIFEST.json'
manifest=json.loads(manifest_path.read_bytes())
actual={str(p.relative_to(CHECKPOINT)):p for p in CHECKPOINT.rglob('*') if p.is_file()}
assert not any(p.is_symlink() for p in CHECKPOINT.rglob('*'))
assert set(actual)==set(manifest['files'])|{'CHECKPOINT_MANIFEST.json'}
for name,expected in manifest['files'].items():
    path=CHECKPOINT/name
    assert path.stat().st_size==expected['bytes'] and hashlib.sha256(path.read_bytes()).hexdigest()==expected['sha256'],name
tracked=set(subprocess.run(['git','-C',str(ROOT),'ls-files','-z','--',str(CHECKPOINT.relative_to(ROOT))],capture_output=True,check=True).stdout.decode().split('\0'))
missing=[str(path.relative_to(ROOT)) for path in actual.values() if str(path.relative_to(ROOT)) not in tracked]
assert not missing,('Checksum-listed or manifest files untracked',missing)
summary=json.loads((CHECKPOINT/'CHECKPOINT_SUMMARY.json').read_bytes())
for name,source in summary['source_copies'].items():
    assert hashlib.sha256((CHECKPOINT/name).read_bytes()).hexdigest()==source['sha256'],name
    original=ROOT/source['original_path']
    assert original.is_file() and original.stat().st_size==source['bytes'] and hashlib.sha256(original.read_bytes()).hexdigest()==source['sha256'],str(original)
print(json.dumps({'status':'PARTIAL_BLOCK_ONLY','checks_status':'NO_FINDINGS','tracked_checkpoint_files':len(actual),'checksum_listed_files':len(manifest['files']),'all_checksum_listed_files_tracked':True,'all_source_copies_byte_identical':True,'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'checkpoint_summary_sha256':hashlib.sha256((CHECKPOINT/'CHECKPOINT_SUMMARY.json').read_bytes()).hexdigest()},sort_keys=True))
