#!/usr/bin/env python3
"""Read-only post-run verification; run via stdin from the benchmark directory."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

import generate_data


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--orchestrator-pid', type=int, required=True)
    parser.add_argument('--source-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Refusing to replace existing integrity evidence')
    proc = Path('/proc') / str(args.orchestrator_pid) / 'cmdline'
    if proc.exists() and b'run_experiment.py' in proc.read_bytes():
        parser.error('Refusing input reads while the benchmark orchestrator is live')
    schedule = json.loads((args.run / 'schedule.json').read_text())
    expected = {(str(scale), engine) for scale in (30, 100, 300)
                for engine in ('polars', 'spark_cpu', 'spark_gpu')}
    assert {(s['scale'], s['engine']) for s in schedule} == expected
    assert all(s.get('status') == 'complete' and s.get('returncode') == 0 for s in schedule)
    for scale, engine in expected:
        session = json.loads((args.run / ('sf' + scale) / engine / 'results.json').read_text())
        assert session.get('completed_at_utc') and len(session['records']) == 24
        assert not session.get('failure') and not session.get('query_errors') and not session.get('cleanup_failure')
    source = Path(generate_data.__file__)
    evidence = {'proof_version': 'postrun-input-integrity-v1',
        'started_at_utc': datetime.now(timezone.utc).isoformat(), 'all_pass': False,
        'scope': 'All five input scales after the final complex run; all listed Parquet bytes rehashed against original manifests.',
        'integrity_script_sha256': args.source_sha256,
        'validator': 'generate_data.validate_existing', 'validator_source_sha256': digest(source),
        'benchmark_schedule_sha256': digest(args.run / 'schedule.json'),
        'datasets': []}
    try:
        for scale, path in ((1, Path('data/sf1')), (10, Path('data/sf10')),
                            (30, Path('data/sf30')), (100, Path('data-large/sf100')),
                            (300, Path('data-large/sf300'))):
            started = time.perf_counter()
            manifest_path = path / 'manifest.json'
            before = digest(manifest_path)
            manifest = generate_data.validate_existing(path, scale)
            assert digest(manifest_path) == before, 'Manifest changed during verification'
            if scale in (30, 100, 300):
                copied = args.run / ('sf' + str(scale)) / 'manifest.json'
                assert digest(copied) == before, 'Measured-run manifest differs from validated input'
                for engine in ('polars', 'spark_cpu', 'spark_gpu'):
                    session = json.loads((copied.parent / engine / 'results.json').read_text())
                    assert session['manifest_sha256'] == before
            files = [item for table in manifest['tables'].values() for item in table['files']]
            row = {'scale_factor': scale, 'input_path': path.as_posix(),
                'manifest_sha256': before, 'parquet_files_checked': len(files),
                'parquet_bytes_checked': sum(f['bytes'] for f in files),
                'tables_checked': len(manifest['tables']),
                'total_rows': sum(t['rows'] for t in manifest['tables'].values()),
                'all_file_hashes_match': True, 'no_missing_or_extra_parquet_files': True,
                'seconds': time.perf_counter() - started}
            evidence['datasets'].append(row)
            print(json.dumps({'event': 'dataset_validated', **row}), flush=True)
        assert Path('data-large/sf30').resolve() == Path('data/sf30').resolve()
        evidence['complex_sf30_alias_matches_verified_dataset'] = True
        evidence['all_pass'] = len(evidence['datasets']) == 5
    except Exception as exc:
        evidence['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    evidence['completed_at_utc'] = datetime.now(timezone.utc).isoformat()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + '.tmp')
    temporary.write_text(json.dumps(evidence, indent=2, allow_nan=False) + '\n')
    temporary.replace(args.output)
    if not evidence['all_pass']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
