#!/usr/bin/env python3
"""Read-only audit of copied block 04; no inference or complete-study PASS."""
from collections import Counter
import copy
from datetime import datetime, timezone
import json
import hashlib
import importlib.util
import inspect
from pathlib import Path
import subprocess
import sys

ROOT = Path('/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study')
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
from operations import reconstruct_h100_replacement as reconstruct
from research_reporting import replay, audit_complete

OUT = Path(__file__).resolve().parent
ARCHIVE = ROOT / 'evidence-research/20261027/h100-confirmatory-gclocker100-v1'
RAW = ROOT / 'rerun/gclocker100-20261027/raw-backups'
BLOCK = RAW / 'block-04'
ARTIFACTS = BLOCK / 'campaign-results'
backup = reconstruct.backup


def require(value, message):
    if not value:
        raise ValueError(message)


def write(path, value):
    with path.open('xb') as handle:
        handle.write(backup.payload(value))


def main():
    report = {'schema_version': 'h100-independent-local-block-audit-v1', 'status': 'PARTIAL_AUDIT_FAILED',
              'scope': 'Block 04 only; campaign incomplete. No statistical analysis, performance estimate, final PASS or final input-byte proof.',
              'started_at_utc': datetime.now(timezone.utc).isoformat(), 'block_id': 'block-04', 'findings': [],
              'audit_script_sha256': backup.sha(__file__)}
    try:
        subprocess.run(['git', '-C', str(ROOT), 'check-ignore', '-q', str(OUT)], check=True)
        canonical_before = reconstruct.inventory(ARCHIVE)
        verified = replay.verify_archive(ARCHIVE)
        plan = verified['plan']
        report.update(campaign_id=plan['campaign_id'], campaign_sha256=verified['campaign_sha256'],
                      prestart_sha256=verified['prestart_sha256'], registered_identity=verified['registered_campaign_identity'],
                      source_commit=plan['source']['git_head'], source_tree_sha256=plan['source']['source_tree_sha256'],
                      source_files_sha256=plan['source']['files_sha256'])
        require(report['campaign_sha256'] == '3142874b918a2df2d66c8dfb32c26f59a09353edcb4cb73e51c4ba46ff387641', 'Unexpected recovery identity')
        scratch = OUT / 'transport-scratch'
        snapshot = reconstruct.verify_snapshot(RAW, 'block-04', report['campaign_sha256'], scratch)
        scratch.rmdir()
        report['transport'] = {'status': 'VERIFIED_PARTIAL_BLOCK', 'control': snapshot['control'],
                               'input_files': snapshot['inputs'], 'actual_files': snapshot['files'],
                               'payload_files': len(snapshot['files']) - 1,
                               'tar_and_original_extracted_directory_independently_verified': True}
        require(snapshot['control']['files'] == 1355 and len(snapshot['files']) == 1356, 'Unexpected transport inventory')
        require(snapshot['control']['tar_bytes'] == 1039677440 and snapshot['control']['tar_sha256'] ==
                '3c54f2fad97149946a914b2ad916e2a2c17bf71dd7792f26a1ef366cc0fec581', 'Unexpected tar bytes/hash')
        for name in ('campaign.json', 'campaign.json.sha256'):
            require(backup.sha(BLOCK / name) == backup.sha(ARCHIVE / name), 'Snapshot campaign bytes differ')
        journal = reconstruct.read_json(ARTIFACTS / 'journal.json')
        selected = [s for s in plan['sessions'] if s['block_id'] == 'block-04']
        require(len(selected) == 40 and len(journal['sessions']) == 400, 'Block design differs')
        require(journal.get('completed_at_utc') is None, 'Partial snapshot cannot claim campaign completion')
        for session in plan['sessions']:
            item = journal['sessions'][session['session_id']]
            if session['block_id'] <= 'block-04':
                require(item['status'] == 'succeeded' and len(item['attempts']) == 1 and
                        item['attempts'][0]['status'] == 'succeeded' and item['attempts'][0]['returncode'] == 0,
                        'Completed block has incomplete or replaced attempt')
            else:
                require(item['status'] == 'planned' and item['attempts'] == [], 'Snapshot includes future block activity')
        validator = ARCHIVE / 'campaign.json.source/research_validate.py'
        frozen_root = validator.parent
        sys.path.insert(0, str(frozen_root))
        spec = importlib.util.spec_from_file_location('_block04_frozen_validate', validator)
        frozen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(frozen)
        require(Path(frozen.__file__).resolve() == validator.resolve(), 'Frozen validator import shadowed')
        audit_plan, digest = frozen.load_plan(ARCHIVE / 'campaign.json')
        require(digest == report['campaign_sha256'] and audit_plan == plan, 'Actual 400-session plan changed')
        audit_plan['_sha256'] = digest  # Frozen validator's own transient digest field; no file rewrite.
        function_source = inspect.getsource(frozen.audit_session).encode()
        with (OUT / 'FROZEN_AUDIT_SESSION_SOURCE.py').open('xb') as handle:
            handle.write(function_source)
        oracle_values, oracle_bindings = {}, {}
        for cell, reference in plan['oracles'].items():
            path = ARCHIVE / 'oracles' / (cell + '.json')
            require(backup.sha(path) == reference['sha256'], 'Canonical oracle hash differs')
            suite, scale = cell.split('-sf')
            oracle_values[cell] = frozen.load_oracle(path, suite, scale, plan['datasets'][scale]['manifest_sha256'])
            oracle_bindings[cell] = {'path': str(path), 'sha256': backup.sha(path)}
        for name in ('research_campaign', 'analyze_results', 'analyze_complex', 'workloads', 'complex_workloads'):
            module = sys.modules.get(name) or __import__(name)
            require(Path(module.__file__).resolve().is_relative_to(frozen_root.resolve()), 'Frozen dependency import shadowed: ' + name)
        report['frozen_module_files_sha256'] = {name: {'path': str(Path(sys.modules[name].__file__).resolve()), 'sha256': backup.sha(sys.modules[name].__file__)} for name in ('research_campaign', 'analyze_results', 'analyze_complex', 'workloads', 'complex_workloads')}
        report['local_helper_files_sha256'] = {str(Path(m.__file__).resolve()): backup.sha(m.__file__) for m in (reconstruct, backup, replay, audit_complete)}
        raw_audits = []
        for session in selected:
            item = journal['sessions'][session['session_id']]
            cell = session['suite'] + '-sf' + session['scale']
            audited = frozen.audit_session(audit_plan, session, item, oracle_values[cell], ARTIFACTS)
            require(audited['status'] == 'PASS' and audited['errors'] == [], 'Frozen selected-session audit failed: ' + session['session_id'])
            raw_audits.append(audited)
        validation = {'schema_version': 'h100-frozen-selected-block-session-audits-v1',
            'status': 'PARTIAL_BLOCK_ONLY', 'scope': 'Only block-04 payload sessions are independently revalidated; original 160-success journal and 400-session plan are retained. Prior block01/02/03 have separately preserved independent proofs. No full CLI or campaign PASS.',
            'campaign_sha256': digest, 'block_id': 'block-04', 'planned_campaign_sessions': 400,
            'journal_succeeded_sessions': sum(item['status'] == 'succeeded' for item in journal['sessions'].values()),
            'audited_sessions': len(raw_audits), 'successful_outputs_compared': sum(len(a['comparisons']) for a in raw_audits),
            'failed_sessions': [a['session_id'] for a in raw_audits if a['status'] != 'PASS'],
            'oracle_files': oracle_bindings, 'sessions': raw_audits,
            'validator_file_sha256': backup.sha(validator), 'audit_session_function_sha256': hashlib.sha256(function_source).hexdigest()}
        require(validation['journal_succeeded_sessions'] == 160 and validation['audited_sessions'] == 40 and validation['successful_outputs_compared'] == 936, 'Selected block validation counts differ')
        write(OUT / 'FROZEN_SELECTED_BLOCK04_AUDITS.json', validation)
        report['frozen_selected_block_validation'] = {'path': str(OUT / 'FROZEN_SELECTED_BLOCK04_AUDITS.json'),
            'sha256': backup.sha(OUT / 'FROZEN_SELECTED_BLOCK04_AUDITS.json'), 'validator_file_sha256': backup.sha(validator),
            'audit_session_function_sha256': hashlib.sha256(function_source).hexdigest(), 'status': validation['status'],
            'sessions': 40, 'successful_outputs_compared': 936, 'journal_succeeded_sessions': 160,
            'oracle_files': oracle_bindings, 'unchanged_plan_sessions': len(audit_plan['sessions']),
            'limitations': 'This isolated payload has only block04: no full --partial CLI run, no altered plan/journal and no claimed new validation of prior block01/02/03.'}
        audits = {a['session_id']: a for a in validation['sessions']}
        readiness = reconstruct.read_json(ARCHIVE / 'readiness/complex-polars_gpu/sf1/polars_gpu/results.json')
        reference_errors = {e['query']: e for e in readiness['query_errors']}
        counts = Counter()
        errors, mappings, gates = [], [], []
        checks = audit_complete.Checks()
        for session in selected:
            item = journal['sessions'][session['session_id']]
            attempt = item['attempts'][0]
            run = ARTIFACTS / Path(attempt['run_root']).relative_to(plan['output_root'])
            value = reconstruct.read_json(run / f"sf{session['scale']}/{session['engine']}/results.json")
            records, failures = value['records'], value.get('query_errors', [])
            require(all(audit_complete.translation_rejection(e, reference_errors[e['query']]) for e in failures),
                    'Failure is not the exact predeclared translation rejection')
            expected = {'cx_q13', 'cx_window'} if session['suite'] == 'complex' and session['engine'] == 'polars_gpu' else set()
            require({e['query'] for e in failures} == expected and len(failures) == len(expected), 'Coverage failure count differs')
            for error in failures:
                errors.append({'session_id': session['session_id'], **error})
            clean, failed_clean = audit_complete.check_cleanup(value, records, failures, checks, session['session_id'])
            counts.update(sessions=1, successful_outputs=len(records), warmup_outputs=sum(r['phase'] == 'warmup' for r in records),
                          measured_outputs=sum(r['phase'] == 'measured' for r in records), translation_rejections=len(failures),
                          successful_complex_cleanups=clean, failed_complex_cleanups=failed_clean)
            audited = copy.deepcopy(audits[session['session_id']])
            actions = {audit_complete.action_key(r): r for r in records}
            frozen_result = Path(attempt['artifacts']['results_path'])
            for entry in audited['plans']:
                if 'source' not in entry:
                    continue
                original = frozen_result.parent / actions[audit_complete.action_key(entry)]['plan']
                local = ARTIFACTS / original.relative_to(plan['output_root'])
                require(entry['source'] == str(local) and backup.sha(local) == entry['sha256'], 'Unexpected plan source/SHA')
                mappings.append({'session_id': session['session_id'], 'from': entry['source'], 'to': str(original), 'sha256': entry['sha256']})
                entry['source'] = str(original)
            gate = item['scientific_validation']
            local_gate = ARTIFACTS / Path(gate['path']).relative_to(plan['output_root'])
            require(gate['status'] == 'PASS' and backup.sha(local_gate) == gate['sha256'] and
                    audited == reconstruct.read_json(local_gate), 'Saved scientific gate does not match independent partial audit')
            gates.append({'session_id': session['session_id'], 'status': 'PASS', 'path': str(local_gate), 'sha256': backup.sha(local_gate)})
        require(not checks.findings, 'Cleanup proof findings')
        require(dict(counts) == {key: value // 10 for key, value in audit_complete.EXPECTED_COUNTS.items()}, 'Per-block action/cleanup totals differ')
        require(len(mappings) == 192, 'Expected 192 path-only Spark GPU plan adaptations')
        write(OUT / 'STRICT_UNSUPPORTED_OUTCOMES.json', {'status': 'PARTIAL', 'count': len(errors), 'outcomes': errors})
        write(OUT / 'SCIENTIFIC_GATE_COMPARISONS.json', {'status': 'PARTIAL', 'scope': '40 gates in block04 only', 'gates': gates,
            'path_only_rule': 'frozen-plan-source-path-v1; comparison copies only; raw validator report unchanged', 'mappings': mappings})
        report.update(counts=dict(counts), strict_unsupported={'count': len(errors), 'type': 'ComputeError',
            'phase': 'warmup', 'trial': 0, 'queries': ['cx_q13', 'cx_window'], 'scales': ['30', '100', '300'],
            'reason': 'strict Polars GPU translation rejections identical in diagnostic shape to sealed readiness'},
            scientific_gate_comparisons=40, path_only_comparison_adaptations=len(mappings), cleanup_checks_evaluated=checks.evaluated)
        require(reconstruct.inventory(ARCHIVE) == canonical_before, 'Canonical prestart changed during audit')
        require(reconstruct.inventory(BLOCK) == snapshot['files'], 'Original block snapshot changed during audit')
        for row in snapshot['inputs'].values():
            require(reconstruct.identity(Path(row['path'])) == {key: row[key] for key in ('sha256', 'bytes')}, 'Transport input changed during audit')
        report['status'] = 'PARTIAL_BLOCK_ONLY'
        report['checks_status'] = 'NO_FINDINGS'
    except BaseException as exc:
        report['findings'].append({'type': type(exc).__name__, 'message': str(exc)})
        raise
    finally:
        report['ended_at_utc'] = datetime.now(timezone.utc).isoformat()
        report['output_files_sha256'] = {name: backup.sha(OUT/name) for name in ('FROZEN_AUDIT_SESSION_SOURCE.py','FROZEN_SELECTED_BLOCK04_AUDITS.json','STRICT_UNSUPPORTED_OUTCOMES.json','SCIENTIFIC_GATE_COMPARISONS.json') if (OUT/name).is_file()}
        write(OUT / 'PARTIAL_BLOCK_AUDIT.json', report)
    print(json.dumps({'status': report['status'], 'report': str(OUT / 'PARTIAL_BLOCK_AUDIT.json'),
                      'sha256': backup.sha(OUT / 'PARTIAL_BLOCK_AUDIT.json'), 'counts': report['counts']}))


if __name__ == '__main__':
    main()
