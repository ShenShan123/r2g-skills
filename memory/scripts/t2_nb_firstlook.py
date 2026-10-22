#!/usr/bin/env python3
"""Eight NB episodes, four workers; subsequent memory construction is a separate step.

Origin-bound leave-one-design-out filtering never infers lineage from an author,
mirror, generic module name, textual mention or coarse candidate reservation.
No other running package or historical result is changed.
"""
from __future__ import annotations

import argparse
import copy
import csv
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO/'memory/scripts'), str(REPO/'memory')]
EVAL = Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')
CORPUS = Path('/data1/zhangdy/RTL/test/rtl-certified-handoff-0002')
INITIAL = CORPUS.parent/'sky130hd_init'
IDS = ('C00876','C02786','C00237','C03579','C02966','C01295','C02881','C00568')
SLOTS = [list(range(lo,lo+8))+list(range(lo+96,lo+104)) for lo in (0,16,32,48)]
CAPS = {'stdcell':.55, 'core':1., 'die':1.}
FLOOR_KEYS = {'stdcell':'floorplan__design__instance__area__stdcell',
              'core':'floorplan__design__core__area','die':'floorplan__design__die__area'}
REFERENCE_POLICY = 'FIRST_UNREPAIRED_NB_FLOORPLAN_V1'


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def load(p):
    return json.loads(Path(p).read_text())


def save(p, value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(value,sort_keys=True,indent=1,allow_nan=False)+'\n')
    tmp.replace(p)


def relationship(a,b):
    if a['handoff_id']==b['handoff_id']:
        return 'EXCLUDE'
    if a.get('verified_ip_key') and a['verified_ip_key']==b.get('verified_ip_key'):
        return 'EXCLUDE'
    ah,bh=set(a.get('core_rtl_hashes') or []),set(b.get('core_rtl_hashes') or [])
    if ah and ah==bh:
        return 'EXCLUDE'
    if ah & bh:
        return 'REVIEW'              # shared support code is not blanket same-IP authority
    return 'KEEP'


def filter_history(units,target,registry):
    keep,drop=[],[]
    for u in units:
        origin=u.get('source_handoff_id')
        if origin not in registry:
            raise ValueError('unbound origin; never guess from text or delete blindly')
        rel=relationship(registry[target],registry[origin])
        if rel=='REVIEW':
            raise ValueError('partial RTL overlap needs a core-IP attribution review')
        (drop if rel=='EXCLUDE' else keep).append(u)
    return keep,drop


def resolve_area_reference(manifest):
    """Derive the predeclared area reference from NB's first flow, before any repair.

    The manifest bytes never change. Binding and successful floorplan provenance
    are checked again on every read; a later repair cannot supply the reference.
    Later arms receive these exact NB references as frozen numeric definitions.
    """
    if manifest.get('area_reference_policy')!=REFERENCE_POLICY:
        return manifest
    p=Path(manifest['source_project'])
    runs=sorted((p/'backend').glob('RUN_*'))
    if not runs:
        raise ValueError('initial reference: no initial execution')
    r=runs[0];binding=r/'effective-config.json';report=r/'logs/2_1_floorplan.json'
    b=load(binding)
    if (b.get('status')!='PASS' or b.get('run_dir')!=str(r)
            or b.get('project_config_sha256')!=manifest['initial_config_sha256']
            or b.get('manifest_sha256')!=sha(p/'manifest.json')):
        raise ValueError('initial reference: input binding invalid')
    stages=[json.loads(s) for s in (r/'stage_log.jsonl').read_text().splitlines() if s.strip()]
    if not any(s.get('stage')=='floorplan' and s.get('status')==0 for s in stages):
        raise ValueError('initial reference: floorplan not successfully measured')
    data=load(report);areas={k:data.get(v) for k,v in FLOOR_KEYS.items()}
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0 for v in areas.values()):
        raise ValueError('initial reference: area metrics missing/invalid')
    receipt={'policy':REFERENCE_POLICY,'run':str(r),'manifest_sha256':sha(p/'manifest.json'),
             'initial_config_sha256':manifest['initial_config_sha256'],'areas':areas,
             'report':str(report),'report_sha256':sha(report),'execution_binding_sha256':sha(binding)}
    target=p/'reports/initial-area-reference.json'
    if target.exists():
        if load(target)!=receipt:
            raise ValueError('initial reference receipt changed')
    else:
        if sha(p/'constraints/config.mk')!=manifest['initial_config_sha256'] or len(runs)!=1:
            raise ValueError('initial reference was not frozen before repair')
        save(target,receipt)
    out=copy.deepcopy(manifest)
    for k in CAPS:
        out['area'][k].update(ref=areas[k],ref_stage='initial_2_1_floorplan',
                              reference_file=str(report),reference_sha256=sha(report))
    return out


def checked_inside(root,path):
    p=Path(path).resolve()
    if not p.is_relative_to(Path(root).resolve()):
        raise ValueError('source escapes certified root: '+str(path))
    return p


def checked_replace(path,before,after):
    text=path.read_text()
    if text.count(before)!=1:
        raise ValueError('unexpected frozen dependency: '+str(path))
    path.write_text(text.replace(before,after,1))


def annotate_clock_period(text, period):
    """Expose the existing literal period to the checker; do not change clock selection."""
    values=re.findall(r'create_clock[^\n]*?\s-period\s+([0-9.]+)',text)
    if not values or any(float(v)!=period for v in values):
        raise ValueError('supplied SDC period is not a consistent literal')
    if re.search(r'\bset\s+clk_period\b',text):
        raise ValueError('unexpected supplied clk_period definition')
    return '# Clock-period metadata only; original create_clock commands unchanged.\nset clk_period '+str(period)+'\n'+text


def prepare(root,base):
    import t2a_batch as B
    import t2a_envelope as E
    import t2a_task_manifests as TM
    root,base=Path(root).resolve(),Path(base).resolve()
    if root.exists():
        raise ValueError('existing preparation: no overwrite/retry')
    old=B.preflight(base,'DEV')
    raw=list(csv.DictReader((CORPUS.parent/'inventory_sky130hd.csv').open()))
    by_id={r['design'].split('__')[0]:r for r in raw}
    reviews={r['id']:r for r in load(EVAL/'t2-candidate-inventory-20261017/t2_independence_review.json')}
    lc=load(EVAL/'t2-lc-v1-20261018c/registration.json')
    lc_ids={t['task'].split('__')[0] for t in lc['tasks']}
    if set(IDS)&lc_ids:
        raise ValueError('learning-curve overlap')
    root.mkdir(parents=True)
    shutil.copytree(base/'code',root/'code')
    shutil.copy2(__file__,root/'code/memory/scripts/t2_nb_firstlook.py')
    # New tasks have no prior floorplan on this host. Derive the frozen reference
    # from the charged initial NB flow, rather than adding a qualification rerun.
    checked_replace(root/'code/memory/scripts/t2a_envelope.py',
        '    specs = manifest.get(\'area\') or {}',
        '    if manifest.get("area_reference_policy"):\n'
        '        from t2_nb_firstlook import resolve_area_reference\n'
        '        manifest = resolve_area_reference(manifest)\n'
        '    specs = manifest.get(\'area\') or {}')
    checked_replace(root/'code/memory/scripts/t2a_runtime.py',
        '    return {\'task\':manifest.get(\'task\'),',
        '    if manifest.get("area_reference_policy"):\n'
        '        from t2_nb_firstlook import resolve_area_reference\n'
        '        manifest = resolve_area_reference(manifest)\n'
        '    return {\'task\':manifest.get(\'task\'),')
    checked_replace(root/'code/memory/scripts/t2a_batch.py',
        "start=time.time();record={'task':task,'arm':arm,'repeat':repeat,'split':split,'paper_data':split=='TEST',",
        "start=time.time();record={'task':task,'arm':arm,'repeat':repeat,'split':split,'paper_data':reg.get('paper_data',split=='TEST'),")
    checked_replace(root/'code/memory/scripts/t2a_batch.py',
        "        initial=H.final_verdict(project,env,project/'eval_arm.log')",
        "        if reg.get('schema')=='nb-firstlook8-v1':\n"
        "            from t2_nb_firstlook import resolve_area_reference\n"
        "            resolve_area_reference(man)\n"
        "            T.event(H,project,'initial_area_reference',{'status':'BOUND_BEFORE_REPAIR','receipt':'reports/initial-area-reference.json'})\n"
        "        initial=H.final_verdict(project,env,project/'eval_arm.log')")
    hook=root/'code/r2g-skills/signoff-loop/scripts/flow/orfs_hooks/buffer_port_feedthroughs.tcl'
    registry={};tasks=[];inputs={}
    for hid in IDS:
        row=by_id[hid];cp=CORPUS/'certified'/row['design'];cert=load(cp/'DESIGN.json')
        review=reviews[hid]
        if any(v['hashes'] or v['modules'] for v in review['overlap'].values()):
            raise ValueError('prior source overlap: '+hid)
        original=E.effective_config((INITIAL/row['run_dir']/'config.mk').read_text())
        repo=checked_inside(CORPUS/'sources',cp/cert['source_dir'])
        expected={checked_inside(repo,cp/f['file']):f['sha256'] for f in cert['source_files']}
        for p,h in expected.items():
            if sha(p)!=h:raise ValueError('certified source changed: '+str(p))
        def source(oldpath):
            if '/sources/' not in oldpath:raise ValueError('unresolved original source path')
            return checked_inside(repo,CORPUS/'sources'/oldpath.split('/sources/',1)[1])
        files=[source(p) for p in original['VERILOG_FILES'].split()]
        if set(files)!=set(expected) or original['DESIGN_NAME']!=cert['top_module']:
            raise ValueError('initial config/source closure mismatch')
        project=root/'tasks'/(row['design']+'__initial')
        (project/'constraints').mkdir(parents=True)
        for p in repo.rglob('*'):checked_inside(repo,p)
        shutil.copytree(repo,project/'rtl')
        cfg=dict(original)
        cfg['VERILOG_FILES']=' '.join(str(project/'rtl'/p.relative_to(repo)) for p in files)
        cfg['VERILOG_INCLUDE_DIRS']=' '.join(str(project/'rtl'/source(p).relative_to(repo)) for p in original['VERILOG_INCLUDE_DIRS'].split())
        cfg['SDC_FILE']=str(project/'constraints/constraint.sdc')
        if cfg['PLATFORM']!='sky130hd' or row['clock_kind']!='real':
            raise ValueError('not a real-clock sky130hd task')
        # The common checker expects literal clk_period metadata. Preserve the
        # original commands/selection exactly; add only that unused Tcl variable.
        original_sdc=INITIAL/row['run_dir']/'constraint.sdc'
        shutil.copy2(original_sdc,project/'constraints/source-constraint.sdc.reference')
        (project/'constraints/constraint.sdc').write_text(annotate_clock_period(original_sdc.read_text(),float(row['period'])))
        (project/'constraints/config.mk').write_text(''.join('export '+k+' = '+v+'\n' for k,v in cfg.items()))
        immutable={'constraints/constraint.sdc':'sha256:'+sha(project/'constraints/constraint.sdc'),
                   'constraints/source-constraint.sdc.reference':'sha256:'+sha(original_sdc),
                   **{str(p.relative_to(project)):'sha256:'+sha(p) for p in (project/'rtl').rglob('*') if p.is_file()},
                   str(hook):'sha256:'+sha(hook)}
        allow=TM.allowlist(cfg);allow['POST_GLOBAL_PLACE_TCL']={'type':'enum','values':[str(hook)]}
        task=row['design']+'/initial'
        manifest={'schema':'t2a-task-envelope-v1','task':task,'platform':'sky130hd','source_project':str(project),
            'initial_config':cfg,'initial_config_sha256':sha(project/'constraints/config.mk'),
            'expected_clock_period_ns':float(row['period']),'stratum':'FIXED_INITIAL_INPUT',
            'immutable':{'knobs':sorted(set(cfg)-set(allow)),'files':immutable},'allowlist':allow,
            'area':{k:{'ref':None,'max_rel_increase':cap,'unit':'um^2'} for k,cap in CAPS.items()},
            'area_reference_policy':REFERENCE_POLICY,'require_execution_binding':True,
            'floorplan_mode_policy':'explicit_util_or_fixed_rectangles','source_handoff_id':hid}
        save(project/'manifest.json',manifest)
        ipkey=('opencores.org/sha3#keccak' if hid=='C00876' else cert['repository']['url'].lower()+'#'+cert['top_module'])
        registry[hid]={'handoff_id':hid,'design_id':cert['design_id'],'task':task,
            'verified_ip_key':ipkey,'owner':cert['repository']['owner'],'repository':cert['repository']['url'],
            'commit':cert['repository']['commit'],'core_rtl_hashes':sorted({h for p,h in expected.items() if p.suffix.lower() in ('.v','.sv')}),
            'independence_review':review,'source_manifest':str(cp/'DESIGN.json')}
        tasks.append({'task':task,'template':str(project),'handoff_id':hid})
        inputs.update({str(cp/'DESIGN.json'):sha(cp/'DESIGN.json'),
                       str(INITIAL/row['run_dir']/'config.mk'):sha(INITIAL/row['run_dir']/'config.mk'),
                       str(INITIAL/row['run_dir']/'constraint.sdc'):sha(INITIAL/row['run_dir']/'constraint.sdc')})
    matrix={a:{b:relationship(registry[a],registry[b]) for b in IDS} for a in IDS}
    if any(matrix[a][b]!=('EXCLUDE' if a==b else 'KEEP') for a in IDS for b in IDS):
        raise ValueError('actual eight-design lineage needs review before launch')
    mock=[{'source_handoff_id':h,'text':'mention every other design'} for h in IDS]
    for h in IDS:
        keep,drop=filter_history(mock,h,registry)
        if len(keep)!=7 or len(drop)!=1:raise ValueError('leave-one-design-out guard failed')
    save(root/'origin-registry.json',registry)
    save(root/'lineage-audit.json',{'status':'PASS','matrix':matrix,'folds':{h:{'excluded':[h],'kept':[x for x in IDS if x!=h]} for h in IDS},
        'not_lineage_authority':['same author','same mirror/host','generic module name','text mention','coarse owner-group'],
        'unknown_origin':'STOP_BEFORE_CONSTRUCTION_NOT_GUESS','same_shared_input_per_fold_for_all_six_memory_methods':True})
    save(root/'episode-budget.json',B.episode_budget())
    save(root/'phase-budget.json',B.formal_phase_budget()|{'budget_id':'nb-firstlook8-independent'})
    (root/'phase').mkdir();(root/'phase/resource_ledger.jsonl').write_bytes(b'')
    shutil.copy2(base/'online-input-authorization.json',root/'online-input-authorization.json')
    save(root/'authorization.json',{'user_text':'同意采用这个“构建记忆前排除当前 design 自身轨迹”的方式，但是在做排除 A 及同源设计的轨迹时不能误伤，没问题后就开始分四路并发用 NB 跑完这 8 个 design，然后记录成本',
        'scope':'eight NB episodes only, four workers, one repeat, three repair generations, no cross-task memory',
        'no_old_2198_history_build':True,'no_six_arm_launch':True,'learning_curve_untouched':True})
    frozen={str(p):sha(p) for sub in ['code','tasks'] for p in (root/sub).rglob('*') if p.is_file()}
    frozen.update({str(root/n):sha(root/n) for n in ['origin-registry.json','lineage-audit.json','episode-budget.json',
        'phase-budget.json','online-input-authorization.json','authorization.json']})
    frozen.update(old['external_hashes']);frozen.update(inputs)
    reg={'schema':'nb-firstlook8-v1','status':'FROZEN_READY','split':'TEST','paper_data':False,'launch_blockers':[],
         'registered_at':time.time(),'tasks':tasks,'arms':['NB'],'repeats':1,
         'plan':[{'task':r['task'],'arm':'NB','repeat':1} for r in tasks],
         'files':frozen,'runtime_hashes':{str(Path(p).relative_to(root/'code')):h for p,h in frozen.items() if Path(p).is_relative_to(root/'code')},
         'external_hashes':old['external_hashes'],'online_policy':B.T.online_policy(),'mechanism_policy':None,
         'scheduler':{'cpu_slots':SLOTS,'concurrency':4,'threads_per_episode':8,'load_log_interval_s':30},
         'phase_binding':{'budget_file':str(root/'phase-budget.json'),'budget_sha256':sha(root/'phase-budget.json'),
             'ledger_path':str(root/'phase/resource_ledger.jsonl'),'prior_ledger_bytes':0,'prior_ledger_sha256':hashlib.sha256(b'').hexdigest()},
         'history_policy':'NB_COLLECTION_THEN_LEAVE_ONE_IP_OUT_BEFORE_EACH_METHOD_CONSTRUCTION',
         'runtime_base':str(base),'no_implicit_retry':True,'scope':'FIRST_LOOK_NOT_CONFIRMATORY'}
    save(root/'registration.json',reg)
    subprocess.run([sys.executable,str(root/'code/memory/scripts/t2a_batch.py'),'preflight','--root',str(root),'--split','TEST'],check=True,
                   env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
    print(json.dumps({'status':reg['status'],'NB_episodes':8,'concurrency':4,'lineage':'PASS'}),flush=True)


def summary(root):
    rows=[]
    for row in load(root/'registration.json')['plan']:
        d=root/'episodes'/(row['task'].replace('/','__')+'__NB__r1');p=d/'episode.json'
        if not p.exists():
            rows.append({**row,'status':'NOT_STARTED'});continue
        e=load(p);led=d/'reports/resource_ledger.jsonl'
        events=[json.loads(s) for s in led.read_text().splitlines() if s.strip()] if led.exists() else []
        rows.append({**row,'episode':str(p),'status':e['status'],'accepted':e.get('accepted'),
            'tokens':e.get('token_usage'),'resources':e.get('resources'),'accounting':e.get('accounting'),
            'worker_wall_s':e.get('total_worker_wall_s'),'tool_calls':sum(x.get('type')=='tool_call' for x in events),
            'checker_runs':sum(x.get('type')=='checker_run' for x in events),'error':e.get('error')})
    save(root/'nb-results.json',{'registered':8,'updated':time.time(),'rows':rows,'complete':all(r['status'] not in ('RUNNING','NOT_STARTED') for r in rows)})


def run(root):
    import t2a_batch as B
    reg=B.preflight(root,'TEST')
    if reg.get('schema')!='nb-firstlook8-v1' or reg['arms']!=['NB']:
        raise ValueError('not this NB registration')
    lease=(root/'supervisor.lock').open('a');fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB)
    with (root/'batch-start.json').open('x') as f:json.dump({'started':time.time(),'registration_sha256':sha(root/'registration.json')},f)
    cpus=[c for slot in SLOTS for c in slot]
    if len(cpus)!=len(set(cpus)) or not set(cpus)<=os.sched_getaffinity(0):raise ValueError('invalid frozen CPUs')
    pending=list(reg['plan']);live={};ended=[];paused=None
    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':''}
    while pending or live:
        for slotid,slot in enumerate(reg['scheduler']['cpu_slots']):
            if paused or slotid in live or not pending:continue
            row=pending.pop(0);name=row['task'].replace('/','__')+'__NB__r1'
            if (root/'episodes'/name).exists():raise RuntimeError('no duplicate episode')
            f=(root/(name+'.worker.log')).open('x')
            cmd=['taskset','-c',','.join(map(str,slot)),sys.executable,str(root/'code/memory/scripts/t2a_batch.py'),
                 'episode','--root',str(root),'--task',row['task'],'--arm','NB','--repeat','1','--split','TEST']
            p=subprocess.Popen(cmd,env=env,stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
            live[slotid]=(p,f,name)
        for sid,(p,f,name) in list(live.items()):
            if p.poll() is None:continue
            f.close();record=root/'episodes'/name/'episode.json'
            e=load(record) if record.exists() else {}
            if p.returncode or e.get('status') not in ('ACCEPTED','NOT_ACCEPTED') or not (e.get('accounting') or {}).get('passed'):
                paused='technical/accounting stop: '+name
            ended.append({'episode':name,'status':e.get('status'),'returncode':p.returncode});del live[sid]
        save(root/'dispatch-state.json',{'status':'PAUSED_DRAINING' if paused and live else 'PAUSED' if paused else 'RUNNING',
            'completed':ended,'running':[dict(episode=n,pid=p.pid,slot=sid) for sid,(p,f,n) in live.items()],
            'pending':len(pending),'reason':paused,'updated':time.time()})
        summary(root)
        with (root/'machine-load.jsonl').open('a') as f:f.write(json.dumps({'ts':time.time(),'loadavg':os.getloadavg(),'slots':SLOTS})+'\n')
        if paused and not live:break
        if pending or live:time.sleep(30)
    save(root/'dispatch-state.json',{'status':'PAUSED' if paused else 'COMPLETED','completed':ended,
        'running':[],'pending':len(pending),'reason':paused,'ended':time.time()})
    summary(root)


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=['prepare','run'])
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--base',type=Path)
    a=ap.parse_args()
    if a.command=='prepare':prepare(a.root,a.base)
    else:run(a.root.resolve())


if __name__=='__main__':main()
