from pathlib import Path
import json,hashlib,datetime,collections
base=Path('/data/yangao/exp2_rerun/runs');out={'collected_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'arms':[],'sources':{}}
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
for name,methods in [('exp2-v5b-20261114',['native-codex','native-dsh']),('exp2-v5fix2-20261115',['r2g-expander-gpt','r2g-expander-deepseek'])]:
 c=base/name/'campaign';m=json.loads((c/'execution_manifest.json').read_text());spec=Path(m['task_spec']['path']);assert digest(spec)==m['task_spec']['sha256'];out['sources'][name]={'campaign':str(c),'task_spec_sha256':digest(spec),'agent_snapshot':m.get('agent_snapshot'),'task_spec':json.loads(spec.read_text())}
 for method in methods:
  b=next(x for x in m['batches'] if x['method_id']==method);mr=next(x for x in m['method_reports'] if x['method_id']==method);assert b['status']==mr['status']=='complete'
  sub=Path(b['submission_path']);ev=Path(b['evaluation_path']);report=Path(mr['report_path']);assert digest(sub)==b['submission_sha256'];assert digest(ev)==b['evaluation_sha256'];assert digest(report)==mr['report_sha256']
  s=json.loads(sub.read_text());e=json.loads(ev.read_text());r=json.loads(report.read_text());times=[datetime.datetime.fromisoformat(x['started_at']) for x in e['results']]+[datetime.datetime.fromisoformat(x['ended_at']) for x in e['results']];start=datetime.datetime.fromisoformat(s['started_at']);end=datetime.datetime.fromisoformat(s['ended_at'])
  counts=collections.Counter();repos=collections.Counter();fingerprints=[]
  for x in e['results']:
   assert x['publishable_qualified'];cells=x['synthesis']['mapped_cells'];counts['small' if cells<1000 else 'medium' if cells<10000 else 'large']+=1;repos[x['key'][0].lower().rstrip('/').removesuffix('.git')]+=1;fingerprints.append(x['synthesis'].get('mapped_netlist_normalized_sha256') or x['source_evidence']['closure_sha256'])
  assert counts=={'small':70,'medium':90,'large':40};assert len(set(fingerprints))==200;assert max(repos.values())<=4
  arm={'method_id':method,'campaign_id':name,'model_route':s['model_route'],'started_at':s['started_at'],'ended_at':s['ended_at'],'timestamp_wall_seconds':(end-start).total_seconds(),'resource_usage':s['resource_usage'],'evaluation_elapsed_seconds':(max(times)-min(times)).total_seconds(),'evaluation_first_started_at':min(times).isoformat(),'evaluation_last_ended_at':max(times).isoformat(),'batch_summary':e['summary'],'method_summary':r['summary'],'independently_recomputed':{'size_counts':dict(counts),'unique_repositories':len(repos),'max_candidates_per_repository':max(repos.values()),'unique_mapped_netlists':len(set(fingerprints))},'evidence':[{'path':str(p),'sha256':digest(p)} for p in [sub,ev,report]]}
  if method=='r2g-expander-deepseek':
   adj=json.loads((c/'reports/runtime_accounting_adjustments.json').read_text())['adjustments'][method];arm['runtime_adjustment']=adj;arm['comparison_runtime_seconds']=adj['comparison_runtime_seconds']
  else:arm['comparison_runtime_seconds']=(end-start).total_seconds()
  out['arms'].append(arm)
print(json.dumps(out,ensure_ascii=False,indent=2))
