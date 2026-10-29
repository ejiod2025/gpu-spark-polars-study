"""Independent raw-action numeric recomputation; writes only this private review report."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import csv,hashlib,io,json,math,statistics,sys
from scipy.stats import t
ROOT=Path('/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study');OUT=Path(__file__).resolve().parent
REPLAY=ROOT/'rerun/gclocker100-20261027/replay-v1';RECON=ROOT/'rerun/gclocker100-20261027/reconstruction-v1';ARC=ROOT/'evidence-research/20261027/h100-confirmatory-gclocker100-v1'
inputs={};evaluated=0;findings=[]
def blob(p):
 p=Path(p);b=p.read_bytes();inputs[str(p)]={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()};return b
def read(p):return json.loads(blob(p))
def rows(p):return list(csv.DictReader(io.StringIO(blob(p).decode())))
def check(v,msg):
 global evaluated
 evaluated+=1
 if not v:raise ValueError(msg)
def close(a,b,label):check(isinstance(a,(int,float)) and not isinstance(a,bool) and math.isclose(a,b,rel_tol=2e-12,abs_tol=0),label+': '+str((a,b)))
def match(a,b,label):
 if isinstance(b,float):close(a,b,label)
 elif isinstance(b,dict):
  check(set(a)==set(b),label+' keys')
  for k,v in b.items():match(a[k],v,label+'/'+k)
 elif isinstance(b,list):
  check(len(a)==len(b),label+' length')
  for i,(x,y) in enumerate(zip(a,b)):match(x,y,label+'/'+str(i))
 else:check(a==b,label+': '+str((a,b)))
def digest(p,want):blob(p);check(inputs[str(p)]['sha256']==want,'identity '+str(p))
def estimate(values,test=False):
 if any(a is None or b is None for a,b in values):return {'status':'incomplete','n_blocks':0,'missing_blocks':BLOCKS,'geometric_mean_speedup':None,'ci95_pointwise':None}
 logs=[math.log(a)-math.log(b) for a,b in values];mean=math.fsum(logs)/10;sd=statistics.stdev(logs);se=sd/math.sqrt(10);margin=float(t.ppf(.975,9))*se
 result={'status':'complete','n_blocks':10,'missing_blocks':[],'geometric_mean_speedup':math.exp(mean),'ci95_pointwise':[math.exp(mean-margin),math.exp(mean+margin)],'mean_log_ratio':mean,'sd_log_ratio':sd,'se_log_ratio':se,'degrees_of_freedom':9,'zero_observed_log_variance':sd==0}
 if test:result['p_raw']=1. if sd==0 and mean==0 else 0. if sd==0 else float(min(1,2*t.sf(abs(mean/se),9)))
 return result
report={'schema_version':'h100-independent-numeric-review-v1','status':'RUNNING','scope':'Independent recomputation from all400 reconstructed results; numeric and manuscript fact review only. No engine/JVM/Parquet/network, raw mutation, publication or editorial/PDF approval.','started_at_utc':datetime.now(timezone.utc).isoformat()}
try:
 digest(REPLAY/'REPLAY_MANIFEST.json','b59450d0731180b5b9cc642c5174f872ab460ab585445b87230175a07c48655c');digest(RECON/'RECONSTRUCTION_MANIFEST.json','6ef171eaaebbda72bfd928b2b12c468a19529e15ec86224d3921c1fcad281b2d');digest(REPLAY/'complete-audit/AUDIT_COMPLETE.json','cb03c639c0b428f50f07a142d1201e8a3c39ad82ea21a2192db95d4ebfc5585d')
 manifest=read(REPLAY/'REPLAY_MANIFEST.json');recon=read(RECON/'RECONSTRUCTION_MANIFEST.json');complete=read(REPLAY/'complete-audit/AUDIT_COMPLETE.json')
 check(manifest['status']=='PASS' and manifest['synthetic'] is False and complete['status']=='PASS' and complete['synthetic'] is False and complete['findings']==[],'genuine complete identities')
 for n,h in manifest['output_files_sha256'].items():digest(REPLAY/n,h)
 plan=read(ARC/'campaign.json');digest(ARC/'campaign.json',manifest['campaign_sha256']);analysis=read(REPLAY/'analysis/analysis.json');validation=read(REPLAY/'validation.json');ART=Path(manifest['artifacts_root']);journal=read(ART/'journal.json')
 check(ART==RECON/'harvest/campaign-results' and recon['status']=='PASS','actual reconstructed raw root')
 check(len(plan['sessions'])==len(journal['sessions'])==400 and journal.get('completed_at_utc') is not None,'complete original journal')
 check(analysis['analysis_source_sha256']==plan['source']['files_sha256']['research_analysis.py'],'frozen analysis source binding')
 for n,h in plan['source']['files_sha256'].items():digest(ARC/'campaign.json.source'/n,h)
 check(plan['analysis_predeclarations']['primary_family_size']==26 and plan['analysis_predeclarations']['multiplicity']=='Holm' and plan['analysis_predeclarations']['primary_endpoint']=='seconds' and plan['analysis_predeclarations']['familywise_alpha']==.05,'predeclared family')
 BLOCKS=[f'block-{i:02d}' for i in range(1,11)];CELLS=[('baseline',str(s)) for s in (1,10,30,100,300)]+[('complex',str(s)) for s in (30,100,300)];CONFIGS=['polars_cpu_144','polars_cpu_200','polars_gpu','spark_cpu','spark_gpu'];ENDPOINTS=['seconds','host_result_seconds'];PAIRS=[('polars_cpu_200','spark_gpu'),('spark_cpu','spark_gpu'),('polars_cpu_144','polars_gpu'),('polars_cpu_200','polars_gpu')]
 saved={s['session_id']:s for s in analysis['sessions']};audited={s['session_id']:s for s in validation['sessions']};index={};medians={};counts=Counter();coverage=Counter()
 for session in plan['sessions']:
  sid=session['session_id'];item=journal['sessions'][sid];check(item['status']=='succeeded' and len(item['attempts'])==1,'raw single success '+sid);attempt=item['attempts'][0]
  def mapped(p):return ART/Path(p).relative_to(plan['output_root'])
  sr=read(mapped(Path(attempt['run_root']).parent/'session.json'));check(sr['session']==session and sr['attempt']==attempt,'raw session binding '+sid)
  p=mapped(attempt['artifacts']['results_path']);v=read(p);digest(p,attempt['artifacts']['results_sha256']);check(audited[sid]['results_sha256']==inputs[str(p)]['sha256'],'validated result hash '+sid)
  check(len(v['queries'])==6,'six original queries '+sid);records=v['records'];errors=v.get('query_errors',[]);keyed={(r['query'],r['phase'],r['trial']):r for r in records};check(len(keyed)==len(records),'unique actions '+sid)
  summaries={};unsupported=set()
  for query in v['queries']:
   got={k for k in keyed if k[0]==query};failure=[e for e in errors if e['query']==query]
   if failure:
    check(session['suite']=='complex' and session['config_id']=='polars_gpu' and query in {'cx_q13','cx_window'} and len(failure)==1 and not got and failure[0]['phase']=='warmup' and failure[0]['trial']==0,'strict unsupported '+sid+'/'+query)
    summaries[query]={'status':'unsupported','endpoints':{},'errors':failure};unsupported.add(query)
   else:
    check(got=={(query,'warmup',0),*((query,'measured',i) for i in (1,2,3))},'all raw action slots '+sid+'/'+query);ends={}
    for endpoint in ENDPOINTS:
     values=[keyed[(query,'measured',i)][endpoint] for i in (1,2,3)];check(all(math.isfinite(x) and x>0 for x in values),'finite measured '+sid)
     ends[endpoint]={'median':sorted(values)[1],'min':min(values),'max':max(values),'trials':values};medians[(sid,query,endpoint)]=ends[endpoint]
    summaries[query]={'status':'complete','endpoints':ends}
  check(unsupported==({'cx_q13','cx_window'} if session['suite']=='complex' and session['config_id']=='polars_gpu' else set()),'fixed unsupported coverage '+sid)
  scores={e:None if unsupported else math.fsum(summaries[q]['endpoints'][e]['median'] for q in v['queries']) for e in ENDPOINTS}
  expected={**{k:session[k] for k in ('session_id','block_id','cell_id','suite','scale','config_id','position')},'queries':summaries,'coverage':'4/6' if unsupported else '6/6','suite_status':'unsupported' if unsupported else 'complete','suite_scores':scores,'results_sha256':inputs[str(p)]['sha256']};match(saved[sid],expected,'session recomputation '+sid)
  index[(session['block_id'],session['suite'],session['scale'],session['config_id'])]=expected;coverage[expected['coverage']]+=1
  counts.update(sessions=1,successful_outputs=len(records),warmup_outputs=sum(r['phase']=='warmup' for r in records),measured_outputs=sum(r['phase']=='measured' for r in records),translation_rejections=len(errors),successful_complex_cleanups=len(v.get('cleanup_records',[])),failed_complex_cleanups=len(v.get('failed_query_cleanup_records',[])))
 match(dict(counts),complete['counts'],'complete counts');check(complete['execution_profile']['profile_gates']==24 and complete['execution_profile']['spark_driver_logs']==160,'complete audited profile counts')
 def pairs(c,e):return [(index[(b,c['suite'],c['scale'],c['numerator'])]['suite_scores'][e],index[(b,c['suite'],c['scale'],c['denominator'])]['suite_scores'][e]) for b in BLOCKS]
 primary=[];primary_index={x['contrast_id']:x for x in analysis['primary']};check(len(primary_index)==26 and set(primary_index)=={x['contrast_id'] for x in plan['primary_contrasts']},'all26 prescribed primary')
 for c in plan['primary_contrasts']:primary.append({**c,'endpoint':'seconds',**estimate(pairs(c,'seconds'),True)})
 previous=0
 for rank,i in enumerate(sorted(range(26),key=lambda i:primary[i]['p_raw'])):
  previous=max(previous,min(1.,(26-rank)*primary[i]['p_raw']));primary[i]['p_holm']=previous
 for c in primary:
  c.update(holm_reject_two_sided_005=c['p_holm']<.05,supported_gpu_advantage=c['p_holm']<.05 and c['geometric_mean_speedup']>1,supported_reference_advantage=c['p_holm']<.05 and c['geometric_mean_speedup']<1);match(primary_index[c['contrast_id']],c,'primary '+c['contrast_id'])
 ratio_rows=rows(REPLAY/'analysis/block_ratios.csv');rprimary=[r for r in ratio_rows if r['family']=='primary_suite'];check(len(rprimary)==260,'260 primary block ratios');rindex={(r['contrast_id'],r['block_id']):r for r in rprimary};check(len(rindex)==260,'unique block ratio keys')
 for c in primary:
  for b,(a,d) in zip(BLOCKS,pairs(c,'seconds')):
   r=rindex[(c['contrast_id'],b)]
   for field,want in [('numerator_seconds',a),('denominator_seconds',d),('ratio',a/d),('log_ratio',math.log(a)-math.log(d))]:close(float(r[field]),want,'raw block ratio '+field)
   check(r['numerator_session_id']==index[(b,c['suite'],c['scale'],c['numerator'])]['session_id'] and r['denominator_session_id']==index[(b,c['suite'],c['scale'],c['denominator'])]['session_id'],'block pair identities')
 pcsv=rows(REPLAY/'figures/primary_contrasts.csv');check(len(pcsv)==26,'primary figure CSV rows')
 for r in pcsv:
  c=primary_index[r['contrast_id']]
  for f,w in [('ratio',c['geometric_mean_speedup']),('ci95_pointwise_lower',c['ci95_pointwise'][0]),('ci95_pointwise_upper',c['ci95_pointwise'][1]),('p_raw',c['p_raw']),('p_holm_family26',c['p_holm'])]:close(float(r[f]),w,'primary figure CSV '+f)
  check(r['n_paired_blocks']=='10' and r['reference_query_coverage']==r['gpu_query_coverage']=='6/6','primary figure coverage')
 scorecells=[];scorecsv=rows(REPLAY/'figures/complete_case_scores.csv');check(len(scorecsv)==40,'40 score cells')
 for r in scorecsv:
  values=[index[(b,r['suite'],r['scale'],r['config_id'])]['suite_scores']['seconds'] for b in BLOCKS];available=all(x is not None for x in values);value=math.exp(math.fsum(math.log(x) for x in values)/10) if available else None
  check(r['planned_blocks']=='10' and r['complete_blocks']==('10' if available else '0') and r['query_coverage']==('6/6' if available else '4/6'),'score coverage')
  if available:close(float(r['geometric_mean_score_seconds']),value,'complete-case score')
  else:check(r['geometric_mean_score_seconds']=='','unavailable score remains empty')
  scorecells.append({'suite':r['suite'],'scale':r['scale'],'config_id':r['config_id'],'complete_blocks':10 if available else 0,'query_coverage':r['query_coverage'],'geometric_mean_score_seconds':value})
 secondary=[];sindex={x['contrast_id']:x for x in analysis['secondary_host_result']};check(len(sindex)==32,'32 secondary pairs')
 for a,d in PAIRS:
  for suite,scale in CELLS:
   c={'contrast_id':f'{a}_over_{d}-{suite}-sf{scale}','numerator':a,'denominator':d,'suite':suite,'scale':scale};stat=estimate(pairs(c,'host_result_seconds'));action=primary_index.get(c['contrast_id']);direction=None if not action or stat['status']=='incomplete' else (action['geometric_mean_speedup']>1)!=(stat['geometric_mean_speedup']>1);entry={**c,'endpoint':'host_result_seconds',**stat,'direction_changed_vs_action_point_estimate':direction};match(sindex[c['contrast_id']],entry,'secondary '+c['contrast_id']);secondary.append(entry)
 changed=[c['contrast_id'] for c in secondary if c['direction_changed_vs_action_point_estimate']];check(changed==analysis['secondary_direction_changes'],'secondary direction change names')
 sensitivity=rows(REPLAY/'complete-audit/HOST_RESULT_SENSITIVITY.csv');check(len(sensitivity)==32,'32 sensitivity rows')
 for r in sensitivity:
  c=sindex[r['contrast_id']];check(r['synthetic']=='False' and r['host_status']==c['status'] and int(r['host_n_blocks'])==c['n_blocks'],'sensitivity coverage')
  if c['status']=='complete':
   a=primary_index[r['contrast_id']]
   for f,w in [('action_ratio',a['geometric_mean_speedup']),('action_ci95_lower',a['ci95_pointwise'][0]),('action_ci95_upper',a['ci95_pointwise'][1]),('host_result_ratio',c['geometric_mean_speedup']),('host_ci95_lower',c['ci95_pointwise'][0]),('host_ci95_upper',c['ci95_pointwise'][1])]:close(float(r[f]),w,'secondary CSV '+f)
   check(r['point_estimate_direction_changed']==str(c['direction_changed_vs_action_point_estimate']),'secondary CSV direction')
  else:check(all(r[f]=='' for f in ['action_ratio','host_result_ratio','host_ci95_lower','host_ci95_upper','point_estimate_direction_changed']),'N/A secondary no imputation')
 medianrows=rows(REPLAY/'analysis/session_query_medians.csv');check(len(medianrows)==4800,'4800 session query endpoint rows')
 for r in medianrows:
  key=(r['session_id'],r['query'],r['endpoint']);v=medians.get(key)
  if v:
   check(r['status']=='complete' and r['nested_action_count']=='3','three nested measured actions');close(float(r['median_seconds']),v['median'],'session-query CSV median');close(float(r['min_seconds']),v['min'],'session-query CSV min');close(float(r['max_seconds']),v['max'],'session-query CSV max')
  else:check(r['status']=='unsupported' and r['nested_action_count']=='0' and r['median_seconds']==r['min_seconds']==r['max_seconds']=='','unsupported median row')
 report.update(status='PASS',checks_status='NO_FINDINGS',counts=dict(counts),coverage_sessions=dict(coverage),primary_family_size=26,primary_paired_blocks=10,primary_block_ratios=260,available_complete_case_scores=sum(r['geometric_mean_score_seconds'] is not None for r in scorecells),score_cells=40,secondary_pairs=32,secondary_available=sum(c['status']=='complete' for c in secondary),secondary_unavailable=sum(c['status']=='incomplete' for c in secondary),secondary_direction_changes=changed,all_primary_holm_rejected=all(c['holm_reject_two_sided_005'] for c in primary),primary_gpu_advantages=sum(c['supported_gpu_advantage'] for c in primary),primary_reference_advantages=sum(c['supported_reference_advantage'] for c in primary),primary_contrasts=primary,complete_case_scores=scorecells,secondary_host_result=secondary,method='Independent median-of-three; six-query fsum; paired log-ratio mean, sample SD/SE and Student-t9 CI/p; one step-down Holm family26. No analysis estimator function called.',manuscript_review_status='PENDING_ACTUAL_EDITS',runtime={'python':sys.version,'scipy':__import__('scipy').__version__})
except BaseException as e:
 report['status']='FAIL';findings.append({'type':type(e).__name__,'message':str(e)});raise
finally:
 report.update(findings=findings,checks_evaluated=evaluated,input_files=inputs,completed_at_utc=datetime.now(timezone.utc).isoformat(),review_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
 target=OUT/'NUMERIC_RECOMPUTATION_REVIEW.json';target.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n')
 print(json.dumps({'status':report['status'],'checks':evaluated,'report':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'findings':findings,'numeric_summary':{k:report.get(k) for k in ['primary_family_size','primary_block_ratios','available_complete_case_scores','score_cells','secondary_pairs','secondary_available','secondary_unavailable','secondary_direction_changes','all_primary_holm_rejected','primary_gpu_advantages','primary_reference_advantages']}},sort_keys=True))
