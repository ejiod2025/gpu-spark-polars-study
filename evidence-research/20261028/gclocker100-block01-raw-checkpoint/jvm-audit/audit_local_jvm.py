"""Read-only partial block-01 JVM/configuration proof; no engine executes."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path('/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study')
sys.path.insert(0, str(ROOT))
from research_reporting import audit_complete as audit
from research_reporting import replay
from operations import jvm_execution_profile as jvm

ARCHIVE = ROOT / 'evidence-research/20261027/h100-confirmatory-gclocker100-v1'
SNAPSHOT = ROOT / 'rerun/gclocker100-20261027/raw-backups/block-01'
OUT = Path(__file__).resolve().parent
inputs, checks = audit.Inputs(), audit.Checks()
checked = replay.verify_archive(ARCHIVE)
plan = inputs.json(ARCHIVE / 'campaign.json')
journal = inputs.json(SNAPSHOT / 'campaign-results/journal.json')
profile = jvm.validate_profile(inputs.json(ARCHIVE / 'EXECUTION_PROFILE.json'))
identity = checked['registered_campaign_identity']
profile_sha = inputs.sha(ARCHIVE / 'EXECUTION_PROFILE.json')
checks.test(inputs.sha(SNAPSHOT / 'campaign.json') == checked['campaign_sha256'], 'snapshot_campaign', 'Snapshot campaign must match canonical seal')
checks.test(identity.get('campaign_id') == plan['campaign_id'] == jvm.CAMPAIGN_ID, 'campaign_id', 'Exact registered recovery ID required')
checks.test(profile_sha == identity['execution_profile_sha256'] == 'a07569ffa3be9cd138ed63084ff59ed668b8bceeef355b5a4f63adef53464c69', 'profile_hash', 'Execution profile must match registered and documented actual profile')
checks.test(profile['source_files_sha256'] == plan['source']['files_sha256'] and profile['python_configs'] == plan['configs'] and profile['settings'] == plan['settings'], 'profile_bindings', 'Profile must retain source, Python configurations and settings')
configs = {c['id']: c for c in plan['configs']}
readiness = inputs.json(ARCHIVE / 'readiness/complex-polars_gpu/sf1/polars_gpu/results.json')
readiness_errors = {e['query']: e for e in readiness['query_errors']}
frozen = audit.load_frozen_checks(ARCHIVE / 'campaign.json.source')
selected = [s for s in plan['sessions'] if s['block_id'] == 'block-01']
expected_cells = {(suite, scale, config) for suite, scale in audit.CELLS for config in audit.CONFIGS}
checks.test(len(selected) == 40 and {(s['suite'],s['scale'],s['config_id']) for s in selected} == expected_cells, 'block_design', 'Block must retain all 40 fixed cells')
checks.test(journal['campaign_sha256'] == checked['campaign_sha256'] and journal.get('completed_at_utc') is None, 'partial_journal', 'Block checkpoint is partial for the registered campaign')
counts = Counter()
sessions, driver_logs = [], []
for session in selected:
    sid = session['session_id']
    before = len(checks.findings)
    item = journal['sessions'][sid]
    checks.test(item.get('status') == 'succeeded' and len(item.get('attempts',[])) == 1, 'single_attempt', 'Exactly one successful attempt required', sid)
    attempt = item['attempts'][0]
    checks.test(attempt.get('status') == 'succeeded' and attempt.get('returncode') == 0 and attempt.get('attempt') == 1, 'attempt_completion', 'Recorded first attempt must have exited zero', sid)
    frozen_run = Path(attempt['run_root'])
    run = audit.mapped(plan, SNAPSHOT / 'campaign-results', frozen_run)
    value_path = run / f"sf{session['scale']}/{session['engine']}/results.json"
    value = inputs.json(value_path)
    checks.test(inputs.sha(value_path) == attempt['artifacts']['results_sha256'], 'result_hash', 'Materialized result hash differs from journal', sid)
    checks.test(inputs.sha(run/'schedule.json') == attempt['artifacts']['schedule_sha256'], 'schedule_hash', 'Schedule hash differs from journal', sid)
    record = inputs.json(run.parent / 'session.json')
    checks.test(record.get('session') == session and record.get('attempt') == attempt and record.get('campaign_sha256') == checked['campaign_sha256'], 'session_binding', 'Raw session record must bind this exact session/attempt/campaign', sid)
    environment = inputs.json(run / 'environment.json')
    audit.check_environment(environment, plan, session, configs[session['config_id']], frozen_run, checks)
    audit.check_config(value, session, readiness, frozen, checks)
    gate_ref = item['scientific_validation']
    gate_path = audit.mapped(plan, SNAPSHOT / 'campaign-results', gate_ref['path'])
    gate = inputs.json(gate_path)
    checks.test(gate_ref.get('status') == gate.get('status') == 'PASS' and not gate.get('errors') and inputs.sha(gate_path) == gate_ref['sha256'], 'scientific_gate', 'Original scientific gate must be hash-bound and passing', sid)
    records, errors = value.get('records',[]), value.get('query_errors',[])
    unsupported = set(audit.DIAGNOSTIC_TOKENS) if session['engine']=='polars_gpu' and session['suite']=='complex' else set()
    expected_actions = {(q,phase,trial) for q in audit.QUERIES[session['suite']] if q not in unsupported for phase,trial in (('warmup',0),('measured',1),('measured',2),('measured',3))}
    actual_actions = [audit.action_key(e) for e in records]
    checks.test(len(actual_actions) == len(set(actual_actions)) and set(actual_actions) == expected_actions, 'all_actions', 'Expected action slots must be exact', sid)
    checks.test({e.get('query') for e in errors} == unsupported and len(errors) == len(unsupported) and all(audit.translation_rejection(e,readiness_errors[e['query']]) for e in errors), 'translation_rejections', 'Only the two expected strict GPU translation shapes may be present', sid)
    comparisons = gate.get('comparisons',[])
    checks.test(Counter(map(audit.action_key,comparisons)) == Counter(actual_actions) and all(c.get('passed') is True for c in comparisons), 'preserved_oracle_gates', 'Preserved scientific gate must retain one passed comparison per successful action', sid)
    clean, failed = audit.check_cleanup(value, records, errors, checks, sid)
    session_counts = {'sessions':1, 'successful_outputs':len(records), 'warmup_outputs':sum(e['phase']=='warmup' for e in records), 'measured_outputs':sum(e['phase']=='measured' for e in records), 'translation_rejections':len(errors), 'successful_complex_cleanups':clean, 'failed_complex_cleanups':failed}
    counts.update(session_counts)
    if session['engine'].startswith('spark_'):
        log = run / f"sf{session['scale']}/{session['engine']}.log"
        flags = jvm.parse_driver_flags(inputs.read(log).decode())
        driver_logs.append({'session_id':sid,'path':str(log),'sha256':inputs.sha(log),'flags':flags})
    sessions.append({'session_id':sid, 'checks': 'MATCH' if len(checks.findings)==before else 'FAIL', 'counts':session_counts})
expected_counts = {'sessions':40,'successful_outputs':936,'warmup_outputs':234,'measured_outputs':702,'translation_rejections':6,'successful_complex_cleanups':336,'failed_complex_cleanups':6}
checks.test(dict(counts) == expected_counts, 'block_counts', 'Block output/cleanup/rejection counts must match frozen design')
checks.test(len(driver_logs)==16, 'driver_count', 'Block must have all 16 actual Spark driver logs')
checks.test(sum(s.get('status')=='succeeded' for s in journal['sessions'].values())==40 and all(item.get('status')=='planned' and item.get('attempts')==[] for sid,item in journal['sessions'].items() if sid not in {s['session_id'] for s in selected}), 'checkpoint_scope', 'Checkpoint must retain exactly block-01 and 360 untouched future sessions')
for path, expected in list(inputs.hashes.items()):
    checks.test(inputs.sha(path)==expected, 'input_stability', 'An inspected input changed during the local audit')
report = {'schema_version':'h100-independent-partial-block-jvm-audit-v1','status':'PARTIAL','scope':'Read-only local block-01 JVM/configuration/runtime proof; no full-study PASS, benchmark execution, statistical analysis or performance estimate', 'checks_status':'NO_FINDINGS' if not checks.findings else 'FAIL','campaign_id':plan['campaign_id'],'campaign_sha256':checked['campaign_sha256'],'prestart_sha256':checked['prestart_sha256'],'execution_profile_sha256':profile_sha,'block_id':'block-01','counts':dict(counts),'expected_counts':expected_counts,'spark_driver_logs':driver_logs,'sessions':sessions,'checks_evaluated':checks.evaluated,'findings':checks.findings,'input_files_sha256':inputs.hashes,'canonical_files_sha256':checked['archive_files_sha256'],'registered_campaign_identity':identity,'source_files_sha256':{str(Path(__file__).resolve()):inputs.sha(__file__),str(Path(audit.__file__).resolve()):inputs.sha(audit.__file__),str(Path(jvm.__file__).resolve()):inputs.sha(jvm.__file__)},'limitations':['Only the 40 block-01 sessions and 16 formal Spark JVM logs are evaluated here.','Block transport payload intentionally omits root profile receipt log; complete-study audit must later prove all 24 idle receipts and 160 formal Spark JVMs.','Installed VM Java binaries are not accessed from this local check; their declared hashes remain bound by the canonical execution profile.','Original scientific gate comparisons are checked as preserved evidence; the parent audit separately runs the frozen partial validator.'], 'completed_at_utc':datetime.now(timezone.utc).isoformat()}
report_path = OUT / 'PARTIAL_BLOCK01_JVM_AUDIT.json'
with report_path.open('xb') as handle: handle.write(audit.canonical_bytes(report))
print(json.dumps({'report':str(report_path),'report_sha256':hashlib.sha256(report_path.read_bytes()).hexdigest(),'status':report['status'],'checks_status':report['checks_status'],'counts':dict(counts),'spark_driver_logs':len(driver_logs),'findings':checks.findings},indent=2))
