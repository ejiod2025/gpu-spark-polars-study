#!/usr/bin/env python3
"""Fresh local stage byte check; no network/science/mutations to original inputs."""
from pathlib import Path
import json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
from operations import h100_private_archive as archive
from operations import research_backup as common
root=Path(__file__).resolve().parents[4]
records=Path(__file__).resolve().parent
stage=root/'rerun/gclocker100-20261027/release-stage-v1'
helper=archive.ref(root/'operations/h100_private_archive.py',root)
execution=archive.ref(Path(__file__),root)
original_ref=archive.ref(records/'STAGING_MANIFEST.json',root)
original=archive.read(records/'STAGING_MANIFEST.json')
expected={a['name']:a for a in original['assets']}
if {p.name for p in stage.iterdir()}!=set(expected):raise ValueError('Stage file set differs')
observed={}
for name,row in expected.items():
 item=archive.ref(stage/name,root)
 if any(item[k]!=row[k] for k in ['bytes','sha256']):raise ValueError('Changed staged bytes: '+name)
 observed[name]=item
joined=[]
for transport in original['original_transports']:
 if len(transport['ordered_asset_names'])>1:
  actual=archive.hash_stream([stage/n for n in transport['ordered_asset_names']])
  source=transport['original_archive']
  if actual!={k:source[k] for k in ['bytes','sha256']}:raise ValueError('Joined split original differs')
  joined.append({'cohort':transport['cohort'],'phase':transport['phase'],'joined':actual,'ordered_asset_names':transport['ordered_asset_names']})
history=archive.read(root/'rerun/replacement-20261027/incidents/aborted-20261027T202748Z/OFFHOST_VERIFIED.json')
transfer=archive.read(root/'rerun/replacement-20261027/incidents/aborted-20261027T202748Z/TRANSFER.json')
historical=observed['historical-default-jvm-abort-evidence.tar.gz']
if any(historical[k]!=history['archive_'+k] or historical[k]!=transfer['archive_'+k] for k in ['bytes','sha256']):raise ValueError('Historical original receipt/control differ')
if history['status']!='OFFHOST_ABORTED_EVIDENCE_VERIFIED' or history['full_campaign_complete'] is not False:raise ValueError('Historical failure role differs')
if archive.ref(root/'operations/h100_private_archive.py',root)!=helper or archive.ref(Path(__file__),root)!=execution:raise ValueError('Verification source changed during execution')
record={'schema_version':'h100-fresh-local-staged-bytes-v1','status':'LOCAL_STAGED_BYTES_REVERIFIED','verified_at_utc':archive.now(),'original_staging_manifest':original_ref,'verification_helper_source':helper,'executed_verification_script':execution,'assets':observed,'assets_verified':len(observed),'bytes_verified':sum(v['bytes'] for v in observed.values()),'split_originals_rejoined_and_verified':joined,'historical_failure_matches_original_receipt_and_transfer':True,'private_download_verification_performed':False,'release_mutations_performed':False}
common.exclusive_json(records/'FRESH_STAGED_BYTES_VERIFICATION.json',record)
correction={'schema_version':'h100-private-staging-source-correction-v1','status':'EXECUTION_SOURCE_DISCREPANCY_DOCUMENTED_LOCAL_BYTES_REVERIFIED','recorded_at_utc':archive.now(),'original_staging_manifest':original_ref,'original_manifest_unchanged':True,'original_manifest_tool_field':original['tool'],'actual_executed_staging_source':archive.ref(records/'EXECUTED_STAGING_TOOL.py',root),'source_provenance_note':archive.ref(records/'STAGING_EXECUTION_SOURCE_NOTE.json',root),'fresh_local_verification':archive.ref(records/'FRESH_STAGED_BYTES_VERIFICATION.json',root),'explanation':'STAGING_MANIFEST.tool hashes the later helper file present at manifest-writing time; it must not be treated as the hash of the loaded staging execution source. Preserve the original manifest unchanged. The exact earlier loaded text is separately preserved; only a guard in the uninvoked catalog command differed. Current helper plus the separately preserved verification script freshly rehashed every staged asset and the joined split original. These are local checks, not private download verification.','private_download_verification_performed':False}
common.exclusive_json(records/'STAGING_SOURCE_CORRECTION.json',correction)
print(json.dumps({'status':record['status'],'assets_verified':len(observed),'bytes_verified':record['bytes_verified'],'verification':archive.ref(records/'FRESH_STAGED_BYTES_VERIFICATION.json',root),'correction':archive.ref(records/'STAGING_SOURCE_CORRECTION.json',root)}))
