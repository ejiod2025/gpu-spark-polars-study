#!/usr/bin/env python3
"""Combine local block05 proofs and separately observed formal profile prefix.

No network, worker, query, performance analysis, journal rewrite or final PASS.
The twelve observed receipts end at block05-after; no later host state is claimed.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path('/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study')
sys.path.insert(0, str(ROOT))
from research_reporting import audit_complete as audit
from research_reporting import replay
from operations import jvm_execution_profile as jvm

OUT = Path(__file__).resolve().parent
ARCHIVE = ROOT / 'evidence-research/20261027/h100-confirmatory-gclocker100-v1'
RAW = ROOT / 'rerun/gclocker100-20261027/raw-backups'
OBSERVATION = ROOT / 'rerun/gclocker100-20261027/observations/20261028T091223Z'
inputs = audit.Inputs()

def require(value, reason):
    if not value:
        raise ValueError(reason)

def main():
    canonical = replay.verify_archive(ARCHIVE)
    plan = canonical['plan']
    block_path, jvm_path = OUT/'PARTIAL_BLOCK_AUDIT.json', OUT/'PARTIAL_BLOCK05_JVM_AUDIT.json'
    block, drivers = inputs.json(block_path), inputs.json(jvm_path)
    for proof in (block, drivers):
        require(proof['status'] == 'PARTIAL_BLOCK_ONLY' and proof['checks_status'] == 'NO_FINDINGS' and proof['findings'] == [], 'A prerequisite independent proof failed')
        require(proof['campaign_sha256'] == canonical['campaign_sha256'] and proof['prestart_sha256'] == canonical['prestart_sha256'], 'Independent proof campaign/prestart differs')
    require(block['counts'] == drivers['counts'] and drivers['counts']['sessions'] == 40 and drivers['counts']['successful_outputs'] == 936, 'Independent proof counts differ')
    require(len(drivers['sessions']) == 40 and all(s['checks'] == 'MATCH' for s in drivers['sessions']) and len(drivers['spark_driver_logs']) == 16, 'Runtime/JVM proof incomplete')
    profile_path = ARCHIVE/'EXECUTION_PROFILE.json'
    profile, profile_sha = jvm.validate_profile(inputs.json(profile_path)), inputs.sha(profile_path)
    require(profile_sha == canonical['registered_campaign_identity']['execution_profile_sha256'] == drivers['execution_profile_sha256'], 'Registered execution profile differs')
    capture_path, gates_path = OBSERVATION/'OBSERVATION.json', OBSERVATION/'EXECUTION_PROFILE_GATES.jsonl'
    capture, gate_bytes = inputs.json(capture_path), inputs.read(gates_path)
    require(capture['schema_version'] == 'h100-readonly-profile-observation-v1' and capture['records'] == 12 and capture['bytes'] == len(gate_bytes) == 29108 and capture['files_sha256']['EXECUTION_PROFILE_GATES.jsonl'] == inputs.sha(gates_path) == '2024c0c587805df4ce81ce26becc427119b4de40d59ce5d4c199fb8c26e5ae5d' and capture['ssh_exit'] == 0, 'Observed formal profile log bytes/count/hash differ')
    sealed_path = ARCHIVE/'EXECUTION_PROFILE_GATES.jsonl'
    sealed = inputs.read(sealed_path)
    require(gate_bytes.startswith(sealed) and sealed.endswith(b'\n') and gate_bytes.endswith(b'\n') and len(sealed.splitlines()) == 2, 'Two sealed readiness receipts are not the untouched log prefix')
    previous_gates_path = ROOT/'rerun/gclocker100-20261027/observations/20261028T071658Z/EXECUTION_PROFILE_GATES.jsonl'
    previous_gate_bytes = inputs.read(previous_gates_path)
    require(len(previous_gate_bytes.splitlines()) == 11 and gate_bytes.startswith(previous_gate_bytes) and
            inputs.sha(previous_gates_path) == 'c551f4af93dfb6ca6530b68a9bff4a364863477a9f6afaecf5679a5f7a846096',
            'Previous eleven observed receipts are not the exact original prefix')
    gates = [audit.strict_json(line, gates_path) for line in gate_bytes.splitlines()]
    require(len(gates) == 12 and [g['phase'] for g in gates] == list(jvm.GATE_PHASES[:12]), 'Observed phase order differs')
    for gate in gates[:12]:
        idle = gate['exclusive_host']
        require(gate['schema_version'] == 'h100-jvm-execution-profile-gate-v1' and gate['status'] == 'PASS' and gate['profile_sha256'] == profile_sha and gate['java_files_sha256'] == profile['java']['files_sha256'] and gate['java_version'] == profile['java']['version_output'] and gate['environment_sources'] == profile['environment_sources'] and gate['environment'] == profile['environment'] and idle['eligible'] is True and idle['nvidia_smi_returncode'] == 0 and idle['gpu_compute_clients'] == idle['nvidia_smi_stderr'] == '' and idle['benchmark_processes'] == [] and audit.utc(idle['observed_at_utc']) <= audit.utc(gate['completed_at_utc']), 'Recorded idle Java/environment mismatch: '+gate['phase'])
    times = [audit.utc(g['completed_at_utc']) for g in gates]
    require(times == sorted(times) and times[-1] <= audit.utc(capture['observed_at_utc']), 'Profile/capture chronology differs')
    journal_path = RAW/'block-05/campaign-results/journal.json'
    journal = inputs.json(journal_path)
    require(journal['campaign_sha256'] == canonical['campaign_sha256'] and journal.get('completed_at_utc') is None and len(journal['sessions']) == 400, 'Original block05 journal scope differs')
    chronology, prior_equal = [], []
    for number, edge in ((1,2),(2,4),(3,6),(4,8),(5,10)):
        block_id = f'block-{number:02d}'
        selected = [s for s in plan['sessions'] if s['block_id'] == block_id]
        require(len(selected) == 40, 'Frozen block design differs')
        earlier = inputs.json(RAW/block_id/'campaign-results/journal.json') if number < 5 else None
        bounds = []
        for session in selected:
            sid = session['session_id']
            item = journal['sessions'][sid]
            require(item['status'] == 'succeeded' and len(item['attempts']) == 1, 'Original session incomplete/replaced: '+sid)
            if earlier is not None:
                require(item == earlier['sessions'][sid], 'Earlier journal item changed: '+sid)
            attempt = item['attempts'][0]
            require(attempt['status'] == 'succeeded' and attempt['returncode'] == 0, 'Attempt did not exit successfully')
            bounds.append((audit.utc(attempt['started_at_utc']),audit.utc(attempt['ended_at_utc'])))
        require(all(a <= b for a,b in bounds) and all(left[1] <= right[0] for left,right in zip(bounds,bounds[1:])), 'Recorded session chronology overlaps')
        require(times[edge] <= bounds[0][0] <= bounds[-1][1] <= times[edge+1], 'Formal profile gates do not bracket '+block_id)
        chronology.append({'block_id':block_id,'sessions':40,'before_at_utc':gates[edge]['completed_at_utc'],'first_started_at_utc':bounds[0][0].isoformat(),'last_ended_at_utc':bounds[-1][1].isoformat(),'after_at_utc':gates[edge+1]['completed_at_utc'],'bracketing':'MATCH','scope':'Original journal attempt chronology; only block05 outputs/JVMs are newly independently audited here'})
        if earlier is not None:
            prior_equal.append({'block_id':block_id,'original_journal_path':str(RAW/block_id/'campaign-results/journal.json'),'sha256':inputs.sha(RAW/block_id/'campaign-results/journal.json'),'selected_session_items_equal_block05_snapshot':True,'items':40})
    protected_sid = 'block-04-complex-sf30-spark_cpu'
    prior_journal_path = RAW/'block-04/campaign-results/journal.json'
    prior_journal = inputs.json(prior_journal_path)
    protected_item = journal['sessions'][protected_sid]
    protected_attempt = protected_item['attempts'][0]
    require(protected_item == prior_journal['sessions'][protected_sid] and
            protected_attempt['orchestrator_wall_seconds'] == 3043.015140947995 and
            protected_attempt['started_at_utc'] == '2026-10-28T04:41:37.045716+00:00' and
            protected_attempt['ended_at_utc'] == '2026-10-28T05:32:20.060856+00:00',
            'Original prior-block04 protected attempt metadata changed')
    wall_time_proof = {'session_id':protected_sid,'current_original_journal_path':str(journal_path),
        'current_original_journal_sha256':inputs.sha(journal_path),'prior_original_journal_path':str(prior_journal_path),
        'prior_original_journal_sha256':inputs.sha(prior_journal_path),'attempt':protected_attempt['attempt'],
        'started_at_utc':protected_attempt['started_at_utc'],'ended_at_utc':protected_attempt['ended_at_utc'],
        'orchestrator_wall_seconds':protected_attempt['orchestrator_wall_seconds'],'status':protected_attempt['status'],
        'returncode':protected_attempt['returncode'],'original_journal_item_equal_prior_block04':True,
        'scope':'Earlier block04 metadata equality only; its outputs and JVMs are not newly revalidated. Original orchestration wall time including the operational hold is preserved verbatim. No subtraction, replacement, exclusion or performance estimate is made.'}
    report = {'schema_version':'h100-independent-block05-partial-summary-v1','status':'PARTIAL_BLOCK_ONLY','checks_status':'NO_FINDINGS','findings':[],
        'scope':'Only block05 payload40 sessions/936 outputs and16 actual Spark JVMs are independently revalidated. All12 separately observed recorded formal profile receipts bracket the first5 blocks. No full CLI, final PASS, statistical analysis, performance estimate or live-host-idle claim.',
        'campaign_id':plan['campaign_id'],'campaign_sha256':canonical['campaign_sha256'],'prestart_sha256':canonical['prestart_sha256'],'execution_profile_sha256':profile_sha,'source_commit':plan['source']['git_head'],'source_tree_sha256':plan['source']['source_tree_sha256'],'block_id':'block-05',
        'counts':block['counts'],'frozen_selected_block_validation':block['frozen_selected_block_validation'],'strict_unsupported':block['strict_unsupported'],'preserved_scientific_gates_compared':block['scientific_gate_comparisons'],'path_only_comparison_adaptations':block['path_only_comparison_adaptations'],
        'session_configuration_and_python_inventories_verified':40,'formal_spark_driver_logs_verified':16,'spark_driver_logs':drivers['spark_driver_logs'],
        'transport':{'status':'VERIFIED_PARTIAL_BLOCK',**block['transport']['control'],'input_files':block['transport']['input_files'],'tar_regular_files_including_backup_manifest':len(block['transport']['actual_files']),'tar_independently_extracted_and_checked':True,'all_original_extracted_files_checked':True,'original_snapshot_and_canonical_prestart_unchanged':True},
        'profile_receipt_proof':{'scope':'Historical recorded formal profile prefix only; observation separate from original raw block05 transport. All12 observed receipts through block05-after are verified. No later host state, current-host-idle or complete24-gate claim.',
            'observation_path':str(OBSERVATION),'capture_sha256':inputs.sha(capture_path),'gate_log_sha256':inputs.sha(gates_path),'gate_log_bytes':len(gate_bytes),'observed_records':12,'verified_records':12,'previous_observation_gate_log_sha256':inputs.sha(previous_gates_path),'previous_observation_exact_prefix_records':11,'sealed_readiness_records':2,'sealed_readiness_gate_log_sha256':inputs.sha(sealed_path),'execution_profile_sha256':profile_sha,'java_files_sha256':profile['java']['files_sha256'],'environment_sources':profile['environment_sources'],'phases':[{'phase':g['phase'],'completed_at_utc':g['completed_at_utc']} for g in gates],'block_chronology':chronology,'prior_journal_equalities':prior_equal},
        'original_journal_succeeded_sessions':200,'original_journal_future_planned_sessions':200,'planned_sessions':400,'prior_sessions_not_newly_oracle_or_jvm_revalidated':160,
        'partial_reports':[{'path':str(p),'sha256':inputs.sha(p)} for p in (block_path,jvm_path)],
        'limitations':['This isolated payload contains only block05; actual original400-session plan and200-success/200-future journal remain unchanged. No whole-journal --partial CLI is run against missing earlier payloads.',
            'Earlier160 successful sessions are journal declarations with original item equality checks only; their output and JVM checks are preserved separately and are not newly counted here.',
            'Actual16 childJVM flags and all40 archived runtime/config inventories are checked; no installedVM Java binary or live process is accessed.',
            'All12 observed recorded formal idle/profile receipts through block05-after are verified; no later block or current host state is assessed. All24 receipts and160 formal Spark JVMs remain a final gate.',
            'Original protected-attempt orchestration wall time including its operational hold is retained exactly; this audit makes no timing correction or performance conclusion.',
            'No true post-final input-byte gate, full400-session completion/replay/analysis, paper/web publication, durable private retrieval or retirement readiness is established.',
            'The separate concurrent contention experiment and same-scheduler handoff/resume disposition require their own evidence and audit. This audit neither changes nor assesses live workloads.'],
        'prior_block04_orchestration_wall_time_preservation':wall_time_proof,
        'audit_runtime':inputs.json(OUT/'AUDIT_RUNTIME.json'),'source_files_sha256':{str(Path(__file__).resolve()):inputs.sha(__file__),str(Path(audit.__file__).resolve()):inputs.sha(audit.__file__),str(Path(jvm.__file__).resolve()):inputs.sha(jvm.__file__),str(Path(replay.__file__).resolve()):inputs.sha(replay.__file__)},'completed_at_utc':datetime.now(timezone.utc).isoformat()}
    for path,digest in list(inputs.hashes.items()):
        require(inputs.sha(path) == digest,'Inspected input changed: '+str(path))
    report['summary_input_files_sha256'] = inputs.hashes
    target=OUT/'PARTIAL_BLOCK05_INDEPENDENT_SUMMARY_WITH_GATES.json'
    with target.open('xb') as f: f.write(audit.canonical_bytes(report))
    print(json.dumps({'status':report['status'],'checks_status':report['checks_status'],'report':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'verified_formal_profile_receipts':12,'observed_records':12,'counts':report['counts'],'block05_chronology':chronology[-1]},sort_keys=True))

if __name__ == '__main__':
    main()
