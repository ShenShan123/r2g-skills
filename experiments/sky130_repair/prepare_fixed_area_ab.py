"""Prepare original-source fixed-area trials using ordinary ingest/planner/apply.
The only reconstructed stage entry is the observed failed route, with provenance;
no successful stages, timing measurements or signoff results are invented.
"""
import hashlib,json,os,re,shutil,sqlite3,subprocess,sys
from pathlib import Path
H=Path(__file__).resolve().parent;REPO=H.parents[1];skill=REPO/'r2g-skills/signoff-loop'
sys.path[:0]=[str(skill/'knowledge')]
import knowledge_db,ingest_run,ab_runner,recipe_lifecycle
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,d):p.write_text(json.dumps(d,indent=2)+'\n')
def main():
 root=Path('/tmp/sky130_aux208/area_recipe');root.mkdir(exist_ok=False)
 db=root/'knowledge.sqlite'
 with sqlite3.connect('file:'+str(skill/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as src,sqlite3.connect(db) as dst:src.backup(dst)
 shutil.copy2(skill/'knowledge/heuristics.json',root/'heuristics.json')
 os.environ.update(R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(db),R2G_HEURISTICS_PATH=str(root/'heuristics.json'),R2G_LAYOUT_OPENROAD='/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
 sources=Path('/tmp/sky130_fixed_area_sources');source_manifest=json.loads((sources/'source_manifest.json').read_text());subjects=[];families={}
 for cid in ['C00315','C00550']:
  frozen=Path('/tmp/sky130_area_subjects')/cid;original=json.loads((frozen/'result.json').read_text());assert original['status']=='fail' and original['stage'].startswith('5_')
  assert sha(frozen/'config.mk')==source_manifest[cid]['original_config_sha256']
  family=next((sources/cid/'rtl').iterdir()).name;project=root/(cid.lower()+'_'+original['design'].split('__')[1]);project.mkdir();shutil.copytree(sources/cid/'rtl',project/'rtl')
  for n,digest in source_manifest[cid]['source_files'].items():assert sha(project/'rtl'/Path(n).relative_to(cid+'/rtl'))==digest
  (project/'constraints').mkdir();(project/'reports').mkdir();backend=project/'backend/RUN_FROZEN_ROUTE_FAILURE';results=backend/'results';results.mkdir(parents=True)
  for n in ['3_place.odb','result.json','config.mk','constraint.sdc']:shutil.copy2(frozen/n,results/n)
  shutil.copy2(frozen/'flow.log',backend/'flow.log')
  # Route-only reconstructed observation: retain original result/log digests.
  event={'stage':'route','status':'fail','provenance':'reconstructed_from_frozen_terminal_result_and_log','source_result_sha256':sha(frozen/'result.json'),'source_log_sha256':sha(frozen/'flow.log'),'source_stage':original['stage']}
  assert re.search(r'\[ERROR GRT-(?:0116|0118|0232)\]',(frozen/'flow.log').read_text())
  (backend/'stage_log.jsonl').write_text(json.dumps(event)+'\n')
  cfg=(frozen/'config.mk').read_text().replace('/proj/workarea/yangao/rtl-certified-handoff-0002/sources/',str(project/'rtl')+'/')
  cfg=re.sub(r'^export SDC_FILE\s*=.*$','export SDC_FILE = '+str(project/'constraints/constraint.sdc'),cfg,flags=re.M)
  (project/'constraints/config.mk').write_text(cfg);shutil.copy2(frozen/'constraint.sdc',project/'constraints/constraint.sdc')
  save(project/'reports/route.json',{'status':'fail','stage':original['stage'],'source_result':str(results/'result.json')})
  save(project/'reports/ppa.json',{'geometry':{'instance_count':original['cells']},'source_result_sha256':sha(frozen/'result.json')})
  save(project/'source_provenance.json',{'case':cid,'design':original['design'],'family':family,'sdc_sha256':sha(project/'constraints/constraint.sdc'),'source_files':{str(p.relative_to(project/'rtl')):sha(p) for p in (project/'rtl').rglob('*') if p.is_file()},'original_config_sha256':sha(frozen/'config.mk'),'failure_observation':event})
  subjects.append(project);families[original['design'].split('__')[1]]=family
 save(root/'families.json',{'mappings':families,'patterns':[]})
 conn=knowledge_db.connect();knowledge_db.ensure_schema(conn);contexts=[];diagnose=skill/'scripts/reports/diagnose_signoff_fix.py'
 for p in subjects:
  rid=ingest_run.ingest(p,conn,families_path=root/'families.json')
  plan=json.loads(subprocess.check_output([sys.executable,'-P',str(diagnose),str(p),'--check','route','--list'],text=True));assert any(s['id']=='fixed_area_route_relief' for s in plan['strategies'])
  contexts.append(plan['routing_context'])
  row=conn.execute('select orfs_status from runs where run_id=?',(rid,)).fetchone();assert row[0]=='fail'
 assert len({c['symptom_id'] for c in contexts})==1 and contexts[0]['symptom_id']
 key={**contexts[0],'strategy':'fixed_area_route_relief'};recipe_lifecycle.enqueue_candidate(conn,provenance='two_family_original_route_failure_area_ab',**key)
 planner=ab_runner.plan_trial(conn,**key,n_designs=2,allowed_project_paths={str(p) for p in subjects})
 if not planner or len(planner['designs'])!=2:raise ValueError('ordinary planner did not select both original failing subjects')
 trials=root/'trials';trials.mkdir();arms=[];h6=hashlib.sha1('|'.join(key[k] for k in ('symptom_id','design_class','platform','strategy')).encode()).hexdigest()[:6].upper()
 for entry in planner['designs']:
  src=Path(entry['project_path']);prov=json.loads((src/'source_provenance.json').read_text())
  for repeat in range(2):
   for role in ['A','B']:
    p=trials/(src.name+'_ab'+role+'_'+key['strategy'][:8]+h6+'_'+str(repeat));shutil.copytree(src,p)
    cfg=p/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(src),str(p)))
    for r in (p/'reports').glob('*.json'):r.write_text(r.read_text().replace(str(src),str(p)))
    if role=='B':save(p/'ab_application.json',json.loads(subprocess.check_output([sys.executable,'-P',str(diagnose),str(p),'--check','route','--rank-first',key['strategy'],'--apply',key['strategy']],text=True)))
    assert sha(p/'constraints/constraint.sdc')==prov['sdc_sha256']
    assert all(sha(p/'rtl'/n)==digest for n,digest in prov['source_files'].items())
    arms.append({'project':str(p),'subject':str(src),'source_repository':prov['family'],'case':prov['design'],'role':role,'repeat':repeat,'config_sha256':sha(cfg),'source_provenance_sha256':sha(p/'source_provenance.json')})
 save(trials/'plan.json',{'key':key,'planner':planner,'arms':arms,'mode':'planned_ab','physical_execution':False});conn.close()
 print(json.dumps({'key':key,'match_level':planner['match_level'],'prepared_arms':len(arms),'physical_execution':False}))
if __name__=='__main__':main()
