#!/usr/bin/env python3
"""Immutable local gate follow-up; prior session audits are hash-linked."""
from datetime import datetime, timezone
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from research_reporting import audit_complete as audit
from research_reporting import replay
from operations import jvm_execution_profile as jvm

OUT = Path(__file__).resolve().parent
ARCHIVE = ROOT / 'evidence-research/20261027/h100-confirmatory-gclocker100-v1'
OBSERVATION = ROOT / 'rerun/gclocker100-20261027/observations/20261028T022143Z'
RAW = ROOT / 'rerun/gclocker100-20261027/raw-backups'
inputs = audit.Inputs()


def require(condition, detail):
    if not condition:
        raise ValueError(detail)


def read_bound(relative, expected):
    path = ROOT / relative
    require(inputs.sha(path) == expected, 'Prior report hash differs: ' + str(path))
    return path, inputs.json(path)


def main():
    prior_path, prior = read_bound(
        'rerun/gclocker100-20261027/independent-block02-audit-20261028T020917Z/PARTIAL_BLOCK02_INDEPENDENT_SUMMARY.json',
        '9d53482afa635ca47a437e3dd599324d6f7051f8a6e505950226dcde28953c2d')
    block01_path, block01 = read_bound(
        'rerun/gclocker100-20261027/independent-block01-jvm-20261028T000656Z/PARTIAL_BLOCK01_JVM_AUDIT_WITH_GATES.json',
        '679d87e10ef0ae3ecf0d6b9f25c9a18f20bf763c9d1b31f7c188e93b14b1e893')
    block02_path, block02 = read_bound(
        'rerun/gclocker100-20261027/independent-block02-jvm-20261028T020849Z/PARTIAL_BLOCK02_JVM_AUDIT.json',
        '0e3bf88f1ab11a6d8920fa639a1e1d21d8034a67fbcecd2887bb2b636485a37e')
    canonical = replay.verify_archive(ARCHIVE)
    plan = canonical['plan']
    profile_path = ARCHIVE / 'EXECUTION_PROFILE.json'
    profile = jvm.validate_profile(inputs.json(profile_path))
    profile_sha = inputs.sha(profile_path)
    identity = canonical['registered_campaign_identity']
    require(profile_sha == identity['execution_profile_sha256'] == prior['execution_profile_sha256'], 'Registered profile mismatch')
    for proof in (block01, block02):
        require(proof['checks_status'] == 'NO_FINDINGS' and proof['findings'] == [] and
                proof['campaign_sha256'] == canonical['campaign_sha256'] == prior['campaign_sha256'] and
                proof['prestart_sha256'] == canonical['prestart_sha256'] == prior['prestart_sha256'] and
                proof['execution_profile_sha256'] == profile_sha and len(proof['spark_driver_logs']) == 16,
                'Prior JVM proof does not bind this exact campaign/profile and 16 logs')
    capture_path = OBSERVATION / 'CAPTURE.json'
    gates_path = OBSERVATION / 'EXECUTION_PROFILE_GATES.jsonl'
    capture = inputs.json(capture_path)
    gate_bytes = inputs.read(gates_path)
    gate_sha = inputs.sha(gates_path)
    require(capture['schema_version'] == 'h100-readonly-profile-observation-v1' and
            capture['bytes'] == len(gate_bytes) == 16981 and capture['records'] == 7 and
            capture['sha256'] == gate_sha == '0ef26fe2f2215e6266cb4e0016963c01d536931734b1288fdddef880c6ccb30a',
            'Separate gate observation bytes/hash/count differ')
    sealed_path = ARCHIVE / 'EXECUTION_PROFILE_GATES.jsonl'
    sealed = inputs.read(sealed_path)
    require(gate_bytes.startswith(sealed) and sealed.endswith(b'\n') and gate_bytes.endswith(b'\n'), 'Sealed readiness prefix changed')
    gates = [audit.strict_json(line, gates_path) for line in gate_bytes.splitlines()]
    require(len(gates) == 7 and [g['phase'] for g in gates] == list(jvm.GATE_PHASES[:7]), 'Observed fixed phase order differs')
    for gate in gates[:6]:
        idle = gate['exclusive_host']
        require(gate['schema_version'] == 'h100-jvm-execution-profile-gate-v1' and gate['status'] == 'PASS' and
                gate['profile_sha256'] == profile_sha and gate['java_files_sha256'] == profile['java']['files_sha256'] and
                gate['java_version'] == profile['java']['version_output'] and gate['environment_sources'] == profile['environment_sources'] and
                gate['environment'] == profile['environment'] and idle['eligible'] is True and idle['nvidia_smi_returncode'] == 0 and
                idle['gpu_compute_clients'] == idle['nvidia_smi_stderr'] == '' and idle['benchmark_processes'] == [] and
                audit.utc(idle['observed_at_utc']) <= audit.utc(gate['completed_at_utc']),
                'Recorded idle Java/environment profile drift: ' + gate['phase'])
    times = [audit.utc(g['completed_at_utc']) for g in gates]
    require(times == sorted(times) and times[-1] <= audit.utc(capture['captured_at_utc']), 'Profile phases/capture chronology differs')
    journal02_path = RAW / 'block-02/campaign-results/journal.json'
    journal01_path = RAW / 'block-01/campaign-results/journal.json'
    journal02, journal01 = inputs.json(journal02_path), inputs.json(journal01_path)
    require(journal01['campaign_sha256'] == journal02['campaign_sha256'] == canonical['campaign_sha256'], 'Journal identity mismatch')
    chronology = []
    for block, edge in (('block-01', 2), ('block-02', 4)):
        selected = [s for s in plan['sessions'] if s['block_id'] == block]
        require(len(selected) == 40, 'Original block design differs')
        bounds = []
        for session in selected:
            sid = session['session_id']
            item = journal02['sessions'][sid]
            require(item['status'] == 'succeeded' and len(item['attempts']) == 1, 'Completed session replaced or incomplete')
            if block == 'block-01':
                require(item == journal01['sessions'][sid], 'Original block01 journal item changed later')
            attempt = item['attempts'][0]
            require(attempt['status'] == 'succeeded' and attempt['returncode'] == 0, 'Attempt did not exit successfully')
            bounds.append((audit.utc(attempt['started_at_utc']), audit.utc(attempt['ended_at_utc'])))
        require(all(start <= end for start, end in bounds) and
                all(left[1] <= right[0] for left, right in zip(bounds, bounds[1:])), 'Original session chronology overlaps')
        require(times[edge] <= bounds[0][0] <= bounds[-1][1] <= times[edge + 1], 'Profile gates fail to bracket ' + block)
        chronology.append({'block_id': block, 'sessions': 40, 'before_at_utc': gates[edge]['completed_at_utc'],
            'first_started_at_utc': bounds[0][0].isoformat(), 'last_ended_at_utc': bounds[-1][1].isoformat(),
            'after_at_utc': gates[edge+1]['completed_at_utc'], 'bracketing': 'MATCH'})
    require(times[5] <= times[6], 'Later block03 observation precedes block02 completion')
    for path, expected in list(inputs.hashes.items()):
        require(inputs.sha(path) == expected, 'Input changed during follow-up')
    report = copy.deepcopy(prior)
    report.update(schema_version='h100-independent-block02-partial-with-gates-v1', status='PARTIAL_BLOCK_ONLY',
                  checks_status='NO_FINDINGS', completed_at_utc=datetime.now(timezone.utc).isoformat())
    report['scope'] = 'Block02 selected40 session audit plus first6 recorded idle profile gates bracketing blocks01/02. Prior block01 session/JVM proofs are only hash-linked. No final PASS, pooled estimates, analysis or remote action.'
    report['root_profile_gate_scope'] = 'First6 recorded gates verified from separately captured observation; seventh block03-before is later operational evidence outside block02 raw snapshot.'
    report['profile_receipt_proof'] = {'observation_path': str(OBSERVATION), 'capture_sha256': inputs.sha(capture_path),
        'gate_log_sha256': gate_sha, 'gate_log_bytes': len(gate_bytes), 'observed_records': 7, 'verified_records': 6,
        'sealed_readiness_gate_log_sha256': inputs.sha(sealed_path), 'execution_profile_sha256': profile_sha,
        'java_files_sha256': profile['java']['files_sha256'], 'environment_sources': profile['environment_sources'],
        'phases': [{'phase': g['phase'], 'completed_at_utc': g['completed_at_utc']} for g in gates], 'block_chronology': chronology,
        'block01_original_journal_items_equal_later_snapshot': True,
        'later_observation': {'phase': 'block-03-before', 'completed_at_utc': gates[6]['completed_at_utc'], 'outside_block02_raw_snapshot': True}}
    report['spark_jvm_proof_links'] = {'scope': '16 previously checked block01 JVMs plus16 independently checked block02 JVMs; no old logs retested here',
        'formal_jvms_supported_by_linked_reports': 32, 'reports': [{'path': str(p), 'sha256': inputs.sha(p), 'logs': 16}
                                                            for p in (block01_path, block02_path)]}
    report['supersedes_only_gate_availability_limitation'] = {'prior_summary_path': str(prior_path), 'sha256': inputs.sha(prior_path), 'prior_report_unchanged': True}
    report['limitations'] = [item for item in prior['limitations'] if 'separate post-block02 profile-gate observation is not included yet' not in item]
    report['limitations'].append('Only the first6 profile receipts are verified here; seventh block03-before is later evidence, and final study still requires24 idle receipts/160 Spark JVMs.')
    report['followup_input_files_sha256'] = inputs.hashes
    report['followup_script_sha256'] = inputs.sha(__file__)
    report_path = OUT / 'PARTIAL_BLOCK02_INDEPENDENT_SUMMARY_WITH_GATES.json'
    with report_path.open('xb') as handle:
        handle.write(audit.canonical_bytes(report))
    print(json.dumps({'status': report['status'], 'checks_status': report['checks_status'], 'report': str(report_path),
        'sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(), 'verified_gates': 6, 'observed_gates': 7, 'chronology': chronology}))


if __name__ == '__main__':
    main()
