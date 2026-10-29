"""Factual numeric/caption review only; no manuscript or editorial changes."""
from pathlib import Path
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv,hashlib,io,json,math,re
ROOT=Path('/Users/danielejiofor/AIRFLOW/gpu-spark-polars-study');OUT=Path(__file__).resolve().parent;REPLAY=ROOT/'rerun/gclocker100-20261027/replay-v1';CONT=ROOT/'rerun/contention-20261028';inputs={};findings=[];checks=0;claims=[]
def blob(p):
 p=Path(p);b=p.read_bytes();inputs[str(p)]={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()};return b
def read(p):return json.loads(blob(p))
def rows(p):return list(csv.DictReader(io.StringIO(blob(p).decode())))
def check(v,label,line=None):
 global checks
 checks+=1
 if not v:findings.append({'message':label,'line':line});raise ValueError(label)
def note(label,lines,evidence):claims.append({'claim_group':label,'manuscript_lines':lines,'status':'MATCH','evidence':evidence})
report={'schema_version':'h100-independent-manuscript-claim-review-v1','status':'RUNNING','scope':'Numeric factual consistency, accounting, ratios, displayed table rounding and exact source captions. No editorial attestation, PDF layout, literature review, publication approval or private-evidence retrieval attestation.','started_at_utc':datetime.now(timezone.utc).isoformat()}
try:
 numeric=read(OUT/'NUMERIC_RECOMPUTATION_REVIEW_WITH_QUERY_ESTIMATES.json');check(numeric['status']=='PASS' and numeric['findings']==[],'independent numeric proof')
 mdpath=ROOT/'publication/h100-confirmatory/MANUSCRIPT.md';text=blob(mdpath).decode();lines=text.splitlines();primary={r['contrast_id']:r for r in numeric['primary_contrasts']};secondary={r['contrast_id']:r for r in numeric['secondary_host_result']};queries={(r['contrast_id'],r['query'],r['endpoint']):r for r in numeric['exploratory_query_contrasts']};scores={(r['suite'],r['scale'],r['config_id']):r for r in numeric['complete_case_scores']}
 def cid(pref,condition):
  suite,scale=condition.split('/');return pref+'-'+('baseline' if suite=='B' else 'complex')+'-sf'+scale
 def cols(line):return [x.strip() for x in line.strip().strip('|').split('|')]
 # Every score display, including all three absent original complex GPU scores.
 nt=0
 for n,line in enumerate(lines,1):
  if re.match(r'^\| (baseline|complex)/\d+ \|',line):
   c=cols(line);suite,scale=c[0].split('/')
   for config,shown in zip(['polars_cpu_144','polars_cpu_200','polars_gpu','spark_cpu','spark_gpu'],c[1:]):
    value=scores[(suite,scale,config)]['geometric_mean_score_seconds'];check(shown==('N/A' if value is None else f'{value:.3f}'),'Table1 score '+str((suite,scale,config)),n);nt+=1
 check(nt==40,'all40 score displays');note('Table1 complete-case scores and coverage',[682,716],'independent raw400 recomputation; 37 numeric/3 N/A;10/10 and6/6 versus0/10 and4/6')
 area=None;pref=None;np=ns=nb=0;raw=[r for r in rows(REPLAY/'analysis/block_ratios.csv') if r['family']=='primary_suite'];rawindex=defaultdict(dict)
 for r in raw:rawindex[r['contrast_id']][r['block_id']]=float(r['ratio'])
 for n,line in enumerate(lines,1):
  if line.startswith('**Table 2'):area='primary'
  elif line.startswith('## Appendix B.'):area='secondary'
  elif line.startswith('## Appendix C.'):area='block'
  elif area=='primary' and line.startswith('### 5.4'):area=None
  m=re.search(r'Prefix `([^`]+)`',line)
  if m:pref=m.group(1)
  if area and re.match(r'^\| [BC]/\d+ \|',line):
   c=cols(line);identity=cid(pref,c[0])
   if area=='primary':
    r=primary[identity];want=[f"{r['geometric_mean_speedup']:.6f}",f"[{r['ci95_pointwise'][0]:.6f}, {r['ci95_pointwise'][1]:.6f}]",f"{r['p_raw']:.5e}",f"{r['p_holm']:.5e}",'GPU' if r['supported_gpu_advantage'] else 'CPU reference'];check(c[1:]==want,'primary table '+identity,n);np+=1
   elif area=='secondary':
    r=secondary[identity]
    if r['status']=='incomplete':want=['N/A','N/A','N/A']
    else:
     a=primary[identity];want=[f"{a['geometric_mean_speedup']:.6f} [{a['ci95_pointwise'][0]:.6f}, {a['ci95_pointwise'][1]:.6f}]",f"{r['geometric_mean_speedup']:.6f} [{r['ci95_pointwise'][0]:.6f}, {r['ci95_pointwise'][1]:.6f}]",'No']
    check(c[1:]==want,'secondary appendix '+identity,n);ns+=1
   else:
    r=primary[identity];values=[rawindex[identity][f'block-{i:02d}'] for i in range(1,11)];want=[', '.join(f'{v:.6f}' for v in values[:5]),', '.join(f'{v:.6f}' for v in values[5:]),f"{r['sd_log_ratio']:.6f}"];check(c[1:]==want,'all10 raw ratios and SD '+identity,n);nb+=1
 check((np,ns,nb)==(26,32,26),'complete primary/secondary/block tables')
 note('Tables2a-2d all26 ratios,CIs,rawp,Holmp and direction',[718,790],'independent t9 log-ratio inference and fixed Holm26; all26 significant,15GPU/11reference')
 note('AppendixB all32 secondary pairs',[1169,1237],'26 available n10;6 N/A n0;no direction change;no secondary tests')
 note('AppendixC all260 block ratios and26 SDs',[1239,len(lines)],'all10 chronological ratios per contrast;sample SD(log ratio), six decimal rounding')
 nq=0
 for n,line in enumerate(lines,1):
  if re.match(r'^\| (baseline|complex) \| `',line):
   c=cols(line);suite,q=c[0],c[1].strip('`')
   for den,shown in zip(['spark_gpu','polars_gpu'],c[2:]):
    r=queries[(f'polars_cpu_200_over_{den}-{suite}-sf300',q,'seconds')];check(shown==('N/A' if r['status']=='incomplete' else f"{r['geometric_mean_speedup']:.3f}"),'Table3 query '+str((suite,q,den)),n)
   nq+=1
 check(nq==12,'all12 SF300 query rows');note('Table3 all12 SF300 query rows and query examples',[798,839],'independent raw median/query-ratio recomputation;24 displays incl2 N/A')
 # Exact source captions for every image, not a newly authored caption.
 def captions(p):
  s=blob(p).decode();return {m.group(1):m.group(2).strip() for m in re.finditer(r'^## ([^\n]+)\n\n(.*?)(?=\n## |\Z)',s,re.M|re.S)}
 cap={**captions(REPLAY/'figures/CAPTIONS.md'),**captions(CONT/'figures-final-v1/CAPTIONS.md')};images=re.findall(r'!\[(.*?)\]\((.*?)\)',text)
 check(len(images)==10,'ten actual manuscript image references')
 for caption,path in images:check(caption==cap[Path(path).stem],'exact caption '+path)
 note('All10 primary/contention figure captions',[716,982],'exact equality with four genuine replay captions and six terminal contention captions')
 # Result interpretation and display-rounding in abstract/body/conclusion.
 check(all(r['holm_reject_two_sided_005'] for r in primary.values()) and sum(r['supported_gpu_advantage'] for r in primary.values())==15 and sum(r['supported_reference_advantage'] for r in primary.values())==11,'all26 primary decisions')
 check(f"{max(r['p_holm'] for r in primary.values()):.3e}"=='5.545e-06' and '5.545e-6' in text,'maxHolmp claim')
 check(not any(r['zero_observed_log_variance'] for r in primary.values()),'nozero variance claim')
 check(all(all((v>1)==(primary[c]['geometric_mean_speedup']>1) for v in rs.values()) for c,rs in rawindex.items()),'all260 observed ratios same direction')
 check(f"{min(r['sd_log_ratio'] for r in primary.values()):.6f}"=='0.015856' and f"{max(r['sd_log_ratio'] for r in primary.values()):.6f}"=='0.109119','observedSD range')
 for c,lo,hi in [('polars_cpu_200_over_spark_gpu-complex-sf30','0.403222','0.537869'),('spark_cpu_over_spark_gpu-complex-sf30','3.439022','4.470605')]:check(f'{min(rawindex[c].values()):.6f}'==lo and f'{max(rawindex[c].values()):.6f}'==hi,'observed ratio extrema '+c)
 for pref in ['spark_cpu_over_spark_gpu','polars_cpu_144_over_polars_gpu','polars_cpu_200_over_polars_gpu']:check(primary[pref+'-baseline-sf1']['supported_reference_advantage'] and all(primary[pref+'-baseline-sf'+str(s)]['supported_gpu_advantage'] for s in (10,30,100,300)),'sampled1to10 bracket '+pref)
 check(all(r['supported_reference_advantage'] for r in primary.values() if r['contrast_id'].startswith('polars_cpu_200_over_spark_gpu')),'CPU200 complete-score advantage all8')
 check(all(primary['spark_cpu_over_spark_gpu-complex-sf'+str(s)]['supported_gpu_advantage'] for s in (30,100,300)),'complex Spark advantage3scales')
 check(queries[('polars_cpu_200_over_spark_gpu-complex-sf300','cx_window','seconds')]['geometric_mean_speedup']>1 and all(queries[(f'polars_cpu_200_over_spark_gpu-baseline-sf{s}','q01','seconds')]['geometric_mean_speedup']>1 for s in (30,100,300)) and all(queries[(f'polars_cpu_200_over_spark_gpu-complex-sf{s}','cx_window','seconds')]['geometric_mean_speedup']>1 for s in (30,100,300)) and all(queries[(f'polars_cpu_200_over_spark_gpu-complex-sf{s}','cx_q13','seconds')]['geometric_mean_speedup']>1 for s in (100,300)),'descriptive query directions')
 note('Abstract,scale brackets,spread,discussion,conclusion',[12,44,780,879,984,1133],'numeric directions/statements match all26 tests/384 query estimates;no exact interpolated threshold;no added significance or causal claim')
 # Count/timestamp/identity claims use original complete journal and actual manifests.
 complete=read(REPLAY/'complete-audit/AUDIT_COMPLETE.json');manifest=read(REPLAY/'REPLAY_MANIFEST.json');ART=Path(manifest['artifacts_root']);j=read(ART/'journal.json');end=[j['sessions'][s['session_id']]['attempts'][0] for s in read(ROOT/'evidence-research/20261027/h100-confirmatory-gclocker100-v1/campaign.json')['sessions']]
 check(end[0]['started_at_utc']=='2026-10-27T21:47:44.042050+00:00' and end[-1]['ended_at_utc']=='2026-10-28T23:26:10.952925+00:00','exact attemptbounds')
 check(j['input_integrity_gates'][-1]['completed_at_utc']=='2026-10-28T23:33:18.564788+00:00' and j['completed_at_utc']=='2026-10-28T23:33:18.565015+00:00','postfinal gate/completion')
 check(complete['counts']==numeric['counts'] and complete['execution_profile']['spark_driver_logs']==160 and complete['execution_profile']['profile_gates']==24,'rawcount profile statements')
 check('23:42:11' in blob(ART.parent/'confirm-driver.ended-utc').decode() and blob(ART.parent/'confirm-driver.exit').decode().strip()=='0','supervisor end and exit')
 check(manifest['ended_at_utc']=='2026-10-28T23:50:00.745311+00:00','replay completion UTC')
 note('Complete-study accounting and genuine timestamps',[618,680,1066,1114],'raw finaljournal,complete-audit counts,manifest identities,supervisor markers')
 # Separate contention numeric claims remain separate from primary inference.
 terminal=read(CONT/'offline-terminal-audit-v1/CONTENTION_REPLAY.json');actions=rows(CONT/'offline-terminal-audit-v1/QUERY_ACTIONS_DESCRIPTIVE.csv');overlap=rows(CONT/'offline-terminal-audit-v1/COMMON_QUERY_OVERLAP.csv')
 check(terminal['status']=='COMPLETE' and terminal['integrity_status']=='PASS' and terminal['publication_ready'] is True and terminal['findings']==[] and terminal['counts']['successful_outputs']==460,'terminal contention identity/count')
 cc=Counter();common=defaultdict(list)
 for a in actions:
  phase='pilot' if a['wave_id'].startswith('pilot-') else 'main';cc[(phase,a['phase'])]+=1
  if a['phase']=='measured' and a['paired_condition']=='True':common[(a['wave_id'],a['query'],a['trial'])].append(a)
 check(cc==Counter({('pilot','warmup'):46,('pilot','measured'):46,('main','warmup'):184,('main','measured'):184}),'contention phase successful counts')
 wins=defaultdict(Counter);exceptions=[]
 for key,pair in common.items():
  check(len(pair)==2,'one participant pair');wave=key[0]
  if not wave.startswith('main-'):continue
  a=next(r for r in pair if r['config_id'].startswith('polars'));s=next(r for r in pair if r['config_id'].startswith('spark'));which='A' if a['config_id']=='polars_cpu_200' else 'B';wins[(which,a['suite'],a['scale'])]['pairs']+=1;polars=float(a['seconds'])<float(s['seconds']);wins[(which,a['suite'],a['scale'])]['polars' if polars else 'spark']+=1
  if not polars:exceptions.append((a['scale'],a['query']))
 nw=0
 for n,line in enumerate(lines,1):
  if line.startswith('| A: ') or line.startswith('| B: '):
   c=cols(line);w=wins[(c[0][0],c[1].lower(),c[2])];check(c[3:]==[str(w['pairs']),str(w['polars']),str(w['spark'])],'contention winner table',n);nw+=1
 check(nw==8 and Counter(exceptions)==Counter({('30','cx_window'):1,('100','cx_window'):2}),'all8 conditions3exceptions')
 for phase,lo,hi,skew in [('pilot','0.208','2.361','0.647'),('main','0.228','8.664','2.340')]:
  rs=[r for r in overlap if r['wave_id'].startswith(phase+'-')];check(f"{min(float(r['overlap_seconds']) for r in rs):.3f}"==lo and f"{max(float(r['overlap_seconds']) for r in rs):.3f}"==hi and f"{1000*max(float(r['actual_start_skew_seconds']) for r in rs):.3f}"==skew,'contention range '+phase)
 for config,q,want in [('spark_gpu','cx_window',['3.176','3.243']),('polars_cpu_200','cx_window',['4.909','4.913']),('spark_gpu','cx_q09',['24.869','23.979']),('polars_cpu_200','cx_q09',['5.999','5.706'])]:
  got=[f"{float(r['seconds']):.3f}" for r in actions if r['wave_id'].startswith('main-') and r['suite']=='complex' and r['scale']=='100' and r['config_id']==config and r['query']==q and r['phase']=='measured'];check(got==want,'contention query examples '+str((config,q)))
 note('Separate contention allnumeric tables/ranges/query examples',[879,963],'terminal audit plus460actionCSV/110overlapCSV;two waves/condition;no primary pooling')
 report.update(status='PASS',checks_status='NO_FINDINGS',display_counts={'score_cells':nt,'primary_rows':np,'secondary_rows':ns,'primary_block_ratios':10*nb,'block_log_SD_rows':nb,'SF300_query_rows':nq,'figure_captions':len(images),'contention_condition_rows':nw},claim_groups=claims,editorial_or_pdf_approval=False)
except BaseException as e:
 report['status']='FAIL'
 if not findings:findings.append({'type':type(e).__name__,'message':str(e)})
 raise
finally:
 report.update(findings=findings,checks_evaluated=checks,input_files=inputs,completed_at_utc=datetime.now(timezone.utc).isoformat(),review_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest());target=OUT/'MANUSCRIPT_NUMERIC_CLAIM_REVIEW.json';target.write_text(json.dumps(report,indent=2,sort_keys=True,allow_nan=False)+'\n');print(json.dumps({'status':report['status'],'checks':checks,'report':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'findings':findings,'display_counts':report.get('display_counts')},sort_keys=True))
