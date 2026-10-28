#!/usr/bin/env python3
"""Copy verified, unchanged block03 metadata into a new private checkpoint.

Raw tar/payload stay ignored. All source JSON/report path strings remain intact.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT=Path('/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study')
OUT=Path(__file__).resolve().parent
CHECKPOINT=ROOT/'evidence-research/20261028/gclocker100-block03-raw-checkpoint'
RAW=ROOT/'rerun/gclocker100-20261027/raw-backups'
OBS=ROOT/'rerun/gclocker100-20261027/observations/20261028T051057Z'
ARCHIVE=ROOT/'evidence-research/20261027/h100-confirmatory-gclocker100-v1'
FAILED=ROOT/'rerun/gclocker100-20261027/independent-block03-audit-20261028T051125Z'

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(8*1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def write(path,value):
    with path.open('xb') as f:f.write((json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n').encode())

def main():
    summary=json.loads((OUT/'PARTIAL_BLOCK03_INDEPENDENT_SUMMARY_WITH_GATES.json').read_bytes())
    assert summary['status']=='PARTIAL_BLOCK_ONLY' and summary['checks_status']=='NO_FINDINGS' and summary['findings']==[]
    assert sha(OUT/'PARTIAL_BLOCK03_INDEPENDENT_SUMMARY_WITH_GATES.json')=='c2f1d4f63ceefb21d984d5e83106cc49d660715f28c2781a2cc8df3390319701'
    assert summary['counts']=={'sessions':40,'successful_outputs':936,'warmup_outputs':234,'measured_outputs':702,'translation_rejections':6,'successful_complex_cleanups':336,'failed_complex_cleanups':6}
    sources={'BACKUP_CONTROL.json':RAW/'block-03.json','BACKUP_MANIFEST.json':RAW/'block-03/BACKUP_MANIFEST.json','OFFHOST_VERIFIED.json':RAW/'block-03.verified.json','JOURNAL.json':RAW/'block-03/campaign-results/journal.json','campaign.json':RAW/'block-03/campaign.json','campaign.json.sha256':RAW/'block-03/campaign.json.sha256','CANONICAL_PRESTART_MANIFEST.json':ARCHIVE/'PRESTART_MANIFEST.json','EXECUTION_PROFILE.json':ARCHIVE/'EXECUTION_PROFILE.json','profile-observation/CAPTURE.json':OBS/'CAPTURE.json','profile-observation/EXECUTION_PROFILE_GATES.jsonl':OBS/'EXECUTION_PROFILE_GATES.jsonl'}
    sources.update({'audit/'+p.name:p for p in sorted(OUT.iterdir()) if p.is_file()})
    sources.update({'audit-environment-attempt-01/'+p.name:p for p in sorted(FAILED.iterdir()) if p.is_file()})
    identities={name:{'original_path':str(path.relative_to(ROOT)),'sha256':sha(path),'bytes':path.stat().st_size} for name,path in sources.items()}
    CHECKPOINT.mkdir()
    for name,path in sources.items():
        target=CHECKPOINT/name
        target.parent.mkdir(parents=True,exist_ok=True)
        with target.open('xb') as f:f.write(path.read_bytes())
        assert sha(target)==identities[name]['sha256']==sha(path),name
    result={'schema_version':'h100-partial-raw-checkpoint-v1','status':'OFFHOST_BLOCK_VERIFIED','independent_validation_status':'PARTIAL_BLOCK_ONLY','checks_status':'NO_FINDINGS','scope':'Third fixed-profile recovery block only; no complete-study result, statistical analysis, performance estimate or live-host-idle claim','created_at_utc':datetime.now(timezone.utc).isoformat(),'campaign_id':summary['campaign_id'],'campaign_sha256':summary['campaign_sha256'],'prestart_sha256':summary['prestart_sha256'],'execution_profile_sha256':summary['execution_profile_sha256'],'block_id':'block-03','planned_sessions':400,'journal_succeeded_sessions':120,'snapshot_pending_sessions':280,'audited_sessions_in_this_checkpoint':40,'prior_sessions_not_newly_oracle_or_jvm_revalidated':80,'counts_in_this_block':summary['counts'],'raw_archive_location':str((RAW/'block-03.tar').relative_to(ROOT)),'raw_archive_sha256':summary['transport']['tar_sha256'],'raw_archive_bytes':summary['transport']['tar_bytes'],'raw_files':1355,'raw_regular_files_including_manifest':1356,'journal_sha256':sha(RAW/'block-03/campaign-results/journal.json'),'frozen_validation_method':'Unchanged archived C84 audit_session on selected40 original sessions with eight canonical oracles, retaining actual original400-session plan and120-success journal; no full --partial CLI against missing prior payloads','formal_spark_driver_logs_verified_in_this_block':16,'session_configuration_and_python_inventories_verified':40,'profile_observation_records':9,'profile_receipts_verified_through_this_block':8,'profile_observation_log_sha256':summary['profile_receipt_proof']['gate_log_sha256'],'profile_observation_log_bytes':21832,'separately_observed_later_receipt':'block-04-before','statistical_analysis_performed':False,'initial_audit_environment_failure':'Preserved separately under audit-environment-attempt-01. Default local Python lacked psutil; failure occurred before any frozen session audit. Successful rerun used existing /tmp/h100-analysis-audit-venv/bin/python -B with no install/network. This is neither a scientific failure nor a benchmark attempt.','independent_summary_sha256':sha(OUT/'PARTIAL_BLOCK03_INDEPENDENT_SUMMARY_WITH_GATES.json'),'frozen_selected_audits_sha256':sha(OUT/'FROZEN_SELECTED_BLOCK03_AUDITS.json'),'local_session_transport_audit_sha256':sha(OUT/'PARTIAL_BLOCK_AUDIT.json'),'jvm_runtime_audit_sha256':sha(OUT/'PARTIAL_BLOCK03_JVM_AUDIT.json'),'source_copies':identities,'limitations':summary['limitations']}
    write(CHECKPOINT/'CHECKPOINT_SUMMARY.json',result)
    readme='''# Recovery block 03 raw checkpoint

Status is **PARTIAL_BLOCK_ONLY, NO_FINDINGS**. All 40 selected block03 sessions and their 936 successful outputs were independently checked locally with the untouched archived C84 `research_validate.audit_session` function and all eight canonical SQL oracles. The six specific strict Polars GPU q13/window translation rejections are warmup/trial0 outcomes at SF30/100/300; all336 successful and6 failed-query complex cleanup records match. The actual original400-session plan and original block-end journal recording120 successes/280 future planned sessions remain unchanged. This isolated transport contains only block03, so no full frozen `--partial` CLI run against missing earlier payloads is claimed. No statistical analysis or performance finding is produced.

The complete 1,042,534,400-byte raw tar has SHA256 `b4f68a4ad9085a470f8214d59915bba3a3cfeb90e46a11a67213e579ee13d90c`. A fresh temporary extraction and the original extracted directory were independently checked against original control/receipt/manifest bytes: all1,355 payload files plus the manifest match. The tar and full payload remain in ignored local operational storage at the exact paths in CHECKPOINT_SUMMARY.json. Original JSON/report path strings are preserved verbatim in this checkpoint; canonical prestart is separately tracked and unchanged.

All40 archived runtime/configuration inventories and16 actual formal Spark childJVM flag tables match the sealed profile: GCLockerRetryAllocationCount100, MaxHeapSize103079215104bytes (96GiB), G1GC, unlocked diagnostic options and PrintFlagsFinal. Installed VM Java binaries and live processes were not accessed.

The independently captured9-record formal profile observation is separate from the raw transport. Its first8 receipts retain the exact two-record sealed readiness prefix and match the sealed Java/environment profile; recorded before/after receipts bracket blocks01/02/03. The ninth is later block04-before evidence outside the block03 snapshot; only its presence, phase and chronology are recorded here. This does not establish current host idleness. Original journal items for the80 earlier block01/02 sessions are unchanged in the block03 journal; their outputs and JVM logs are not newly revalidated or counted here.

The first local audit invocation used a default Python missing psutil and stopped at the archived research_campaign import before any scientific session audit. Its exact script, failure report and log remain in audit-environment-attempt-01. The successful rerun used the existing pinned local interpreter `/tmp/h100-analysis-audit-venv/bin/python -B`; AUDIT_RUNTIME.json records its versions. No dependency installation, network, API, VM operation, worker/query execution, restart or performance analysis was used. Three successful verification logs and all exact audit scripts/reports are preserved under audit/.

The study remains incomplete. The final gates still require all400 sessions, all9,360 successful outputs/60 expected rejections, every raw block, all24 formal profile receipts/160 actual Spark JVMs, the genuine post-final input-byte gate, complete reconstruction/replay/analysis, separate contention disposition and same-scheduler handoff/resume proof, full paper and public webpage, durable private evidence retrieval, and conditional retirement. The existing isolated scheduler is held for the separately authorized contention experiment; this local audit neither changes nor assesses live jobs.

CHECKPOINT_MANIFEST.json lists the SHA256 and byte count of every checkpoint file except itself. `audit/verify_block03_checkpoint.py` verifies the file set/checksums and that every listed file is tracked in Git. Verification logs are intentionally force-added despite the global *.log ignore rule. No raw tar or payload is committed, and no push is performed by this checkpoint operation.
'''
    with (CHECKPOINT/'README.md').open('x') as f:f.write(readme)
    files={str(p.relative_to(CHECKPOINT)):{'sha256':sha(p),'bytes':p.stat().st_size} for p in sorted(CHECKPOINT.rglob('*')) if p.is_file()}
    write(CHECKPOINT/'CHECKPOINT_MANIFEST.json',{'schema_version':'h100-private-checkpoint-inventory-v1','status':'PARTIAL_BLOCK_ONLY','campaign_sha256':summary['campaign_sha256'],'block_id':'block-03','scope':'Checksums of private checkpoint metadata; excludes this manifest itself. Raw tar/payload stay ignored.','files':files})
    print(json.dumps({'checkpoint':str(CHECKPOINT),'files_including_manifest':len(files)+1,'manifest_sha256':sha(CHECKPOINT/'CHECKPOINT_MANIFEST.json'),'summary_sha256':sha(CHECKPOINT/'CHECKPOINT_SUMMARY.json'),'independent_summary_sha256':result['independent_summary_sha256']},sort_keys=True))

if __name__=='__main__':main()
