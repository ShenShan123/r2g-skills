"""Exercise ordinary CLI on real failed inputs, with an isolated learning store."""
from pathlib import Path
import os,sys,subprocess,json,shutil,sqlite3,hashlib,argparse,re
parser=argparse.ArgumentParser();parser.add_argument('--label',default='');label=parser.parse_args().label
assert re.fullmatch(r'[a-z0-9_]*',label)
suffix='_'+label if label else ''
H=Path(__file__).resolve().parent;repo=H.parents[1];root=Path('/tmp/sky130_fixed_area_cli'+suffix);root.mkdir(exist_ok=False)
skill=repo/'r2g-skills/signoff-loop';db=root/'knowledge.sqlite'
with sqlite3.connect('file:'+str(skill/'knowledge/knowledge.sqlite')+'?mode=ro',uri=True) as src,sqlite3.connect(db) as dst:src.backup(dst)
shutil.copy2(skill/'knowledge/heuristics.json',root/'heuristics.json')
env=dict(os.environ,R2G_JOURNAL='0',R2G_KNOWLEDGE_DB=str(db),R2G_HEURISTICS_PATH=str(root/'heuristics.json'),R2G_LAYOUT_OPENROAD='/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad')
for key in ['PYTHONHOME','PYTHONPATH','PYTHONSAFEPATH']:env.pop(key,None)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
reports=[]
for cid in ['C00315','C00550']:
 source=Path('/tmp/sky130_area_subjects')/cid;project=root/cid;shutil.copytree(source,project)
 (project/'constraints').mkdir();(project/'reports').mkdir()
 for name in ['config.mk','constraint.sdc']:shutil.copy2(source/name,project/'constraints'/name)
 original=json.loads((source/'result.json').read_text());assert original['status']=='fail' and original['stage'].startswith('5_')
 (project/'reports/route.json').write_text(json.dumps({'status':'fail','stage':original['stage'],'failure_class':original['failure_class'],'source':str(source/'result.json'),'source_sha256':sha(source/'result.json')}))
 cfg=project/'constraints/config.mk';sdc=project/'constraints/constraint.sdc';before=cfg.read_text();sdc_before=sha(sdc)
 args=[sys.executable,'-P',str(skill/'scripts/reports/diagnose_signoff_fix.py'),str(project),'--check','route']
 results={}
 for mode,extra in [('list',['--list']),('auto',['--next']),('blocked_apply',['--apply','fixed_area_route_relief']),('ab_apply',['--rank-first','fixed_area_route_relief','--apply','fixed_area_route_relief'])]:
  r=subprocess.run(args+extra,env=env,capture_output=True,text=True,timeout=60)
  results[mode]={'returncode':r.returncode,'stdout':r.stdout,'stderr':r.stderr}
  if mode=='list':assert any(s['id']=='fixed_area_route_relief' for s in json.loads(r.stdout)['strategies'])
  if mode=='auto':assert 'fixed_area_route_relief' not in r.stdout
  if mode=='blocked_apply':assert r.returncode==5 and cfg.read_text()==before
  if mode=='ab_apply':assert r.returncode==0 and cfg.read_text()!=before
 assert sha(sdc)==sdc_before
 reports.append({'case':cid,'results':results,'sdc_unchanged':True,'baseline_files':{p.name:sha(p) for p in source.iterdir() if p.is_file()},'physical_execution':False})
 print(cid,'ordinary CLI candidate listed, normal apply blocked, A/B apply verified',flush=True)
(H/('fixed_area_cli_preflight_20261027'+suffix+'.json')).write_text(json.dumps(reports,indent=2)+'\n')
