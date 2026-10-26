#!/usr/bin/env python3
"""Read the live benchmark state without changing or restarting remote jobs."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess


REMOTE_CODE = r'''
import datetime, json, pathlib, shutil, sys
root = pathlib.Path(sys.argv[1])
run = root / sys.argv[2]
pid = int(sys.argv[3]) if sys.argv[3] else None
proc = pathlib.Path('/proc') / str(pid) if pid is not None else None
cmdline = (proc / 'cmdline').read_bytes().replace(b'\0', b' ').decode() if proc is not None and proc.exists() else None
result = {
    'checked_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'orchestrator_pid': pid,
    'orchestrator_live': bool(cmdline and 'run_experiment.py' in cmdline) if pid is not None else None,
    'disk_free_gib': round(shutil.disk_usage(root).free / 2**30, 3),
    'sessions': [],
}
for source in sorted(run.glob('sf*/*/results.json')):
    value = json.loads(source.read_text())
    result['sessions'].append({
        'scale': source.parent.parent.name, 'engine': source.parent.name,
        'outputs': len(value.get('records', [])),
        'passed_cleanups': sum(c.get('status') == 'passed' for c in value.get('cleanup_records', [])),
        'query_errors': len(value.get('query_errors', [])),
        'failure': value.get('failure'), 'cleanup_failure': value.get('cleanup_failure'),
        'completed_at_utc': value.get('completed_at_utc'),
        'active_query': value.get('active_query'),
    })
schedule = run / 'schedule.json'
result['schedule'] = [
    {k: item.get(k) for k in ('scale', 'engine', 'status', 'returncode', 'process_wall_seconds')}
    for item in json.loads(schedule.read_text())
] if schedule.exists() else []
print(json.dumps(result, indent=2))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identity', required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--orchestrator-pid', type=int)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--brief', action='store_true')
    args = parser.parse_args()
    command = ['ssh', '-i', args.identity, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
               '-o', 'ConnectTimeout=15', args.host, '.venv/bin/python -c ' + shlex.quote(REMOTE_CODE)]
    command[-1] = ('cd ' + shlex.quote(args.workspace) + ' && ' + command[-1] + ' ' +
                   ' '.join(shlex.quote(v) for v in [args.workspace, args.run,
                       str(args.orchestrator_pid) if args.orchestrator_pid is not None else '']))
    captured = subprocess.run(command, text=True, capture_output=True, check=True, timeout=45)
    value = json.loads(captured.stdout)
    if args.output:
        args.output.write_text(json.dumps(value, indent=2) + '\n')
    if args.brief:
        value = {k: value[k] for k in ('checked_at_utc', 'orchestrator_live', 'disk_free_gib')} | {
            'completed_sessions': sum(bool(s['completed_at_utc']) for s in json.loads(captured.stdout)['sessions']),
            'active_or_failed_sessions': [s for s in json.loads(captured.stdout)['sessions']
                if not s['completed_at_utc'] or s['query_errors'] or s['failure'] or s['cleanup_failure']],
        }
    print(json.dumps(value, indent=2))


if __name__ == '__main__':
    main()
