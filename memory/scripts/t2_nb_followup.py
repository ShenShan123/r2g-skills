#!/usr/bin/env python3
"""Once-only NB -> origin-filtered memory construction -> 48 memory episodes.

Never edit/re-run the eight NB episodes or the separate learning curve. Construction
uses public NB logs, not RTL/SDC, and stays isolated per held-out target BEFORE
any insight/note/workflow induction. Technical/UNKNOWN faults pause, never retry.
"""
from __future__ import annotations
import argparse
import copy
import csv
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(REPO/'memory/scripts'),str(REPO/'memory')]
import t2_nb_firstlook as N

ARMS=('RET','RECIPE','EXPEL','AWM','AMEM','TM0')
EMBEDDING_PYTHON=Path('/data1/zhangdy/pyenvs/t2a-embeddings-20261011/bin/python')
EMBEDDING_LIB=Path('/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/lib')
SLOTS=[list(range(lo,lo+8))+list(range(lo+96,lo+104)) for lo in (0,10,20,30,40,50,60)]
UPSTREAM=('LeapLabTHU_ExpeL/agent/expel.py','WujiangXu_A-mem/memory_layer.py',
          'zorazrw_agent-workflow-memory/mind2web/prompt/instruction_abstract.txt')
sha,load,save=N.sha,N.load,N.save


def rows(p):
    return [json.loads(s) for s in Path(p).read_text().splitlines() if s.strip()] if Path(p).exists() else []


def frozen(root):
    return {str(p):sha(p) for p in Path(root).rglob('*') if p.is_file() and '__pycache__' not in p.parts}


def verify_files(files):
    for p,h in files.items():
        if sha(p)!=h:raise ValueError('frozen input changed: '+p)


def nb_gate(nb):
    """A measured NB failure is eligible; an incomplete/technical cell is not."""
    reg=load(nb/'registration.json');state=load(nb/'dispatch-state.json')
    if reg.get('schema')!='nb-firstlook8-v1' or reg['arms']!=['NB'] or len(reg['plan'])!=8:
        raise ValueError('wrong NB source')
    if state.get('status')=='RUNNING':return False
    if state.get('status')!='COMPLETED':raise ValueError('NB queue stopped technically; no automatic bypass')
    for r in reg['plan']:
        p=nb/'episodes'/(r['task'].replace('/','__')+'__NB__r1')
        e=load(p/'episode.json')
        if e.get('status') not in ('ACCEPTED','NOT_ACCEPTED') or not e.get('accounting',{}).get('passed'):
            raise ValueError('NB cell is not a complete measured outcome: '+r['task'])
        ev=rows(p/'reports/resource_ledger.jsonl')
        for q in [v for v in ev if v.get('type')=='llm_request']:
            u=q.get('usage_reported')
            if not isinstance(u,dict) or any(not isinstance(u.get(k),(int,float)) for k in ('prompt_tokens','completion_tokens')):
                raise ValueError('NB UNKNOWN usage blocks followup')
        import t2a_runtime as T
        if not T.accounting_check(ev,list((p/'backend').glob('RUN_*')))['passed']:
            raise ValueError('NB raw accounting does not reproduce: '+r['task'])
    return True


def raw_units(nb,registry):
    """Lossless raw rows + one compact, origin-bound episode context per design.

    LLM candidate events remain raw events, never fabricated fix-log rows. Where
    the existing verifier cannot bind them they stay unadmitted, with proposals,
    outcomes and rollback still available identically to all methods.
    """
    units=[];inputs={}
    for hid,origin in registry.items():
        p=nb/'episodes'/(origin['task'].replace('/','__')+'__NB__r1');e=load(p/'episode.json')
        session='nb8:'+hid;trace={'task':origin['task'],'strict_status':e['status'],
            'initial_config':load(p/'manifest.json')['initial_config'],
            'initial_failed_checks':(e.get('initial',{}).get('acceptance') or {}).get('failed'),
            'initial_accepted':e.get('initial',{}).get('accepted'),
            'terminal_reason':e.get('terminal_reason'),'cost':{'resources':e.get('resources'),
                'tokens':e.get('token_usage'),'worker_wall_s':e.get('total_worker_wall_s')},
            'LLM_candidates':[{k:r.get(k) for k in ('turn','repair_turn_id','situation','proposal',
                'status','reason','applied','flow_launched','flow_rc','rolled_back','comparison','usage')}
                for r in e.get('repair_turns',[])]}
        initial_manifest=p/'manifest.json';inputs[str(initial_manifest)]=sha(initial_manifest)
        platform=load(initial_manifest)['platform']
        inputs[str(p/'episode.json')]=sha(p/'episode.json')
        led=p/'reports/resource_ledger.jsonl';inputs[str(led)]=sha(led)
        entries=[]
        for file,kind,selected in [(p/'reports/fix_log.jsonl','fix',lambda r:True),
                                   (led,'llm',lambda r:r.get('type')=='repair_candidate' and not r.get('journal_ref'))]:
            if not file.exists():continue
            inputs[str(file)]=sha(file)
            for i,line in enumerate(file.read_text().splitlines(),1):
                if line.strip():
                    raw=json.loads(line)
                    if selected(raw):entries.append((raw,{'path':str(file),'line':i},kind))
        entries.append(({'check':'episode_summary','strategy':'none','from_stage':None,
            'fix_session_id':session,'iter':0,'ts':str(e['started'])},None,'summary'))
        for i,(raw,ref,kind) in enumerate(entries):
            u={'unit_id':'nb8:'+hid+':'+kind+':'+hashlib.sha256(json.dumps(raw,sort_keys=True).encode()).hexdigest()[:20],
                'identity':{'design':hid,'platform':platform,'ts':str(raw.get('ts',e['started']))},
                'raw_row':raw,'action':{'strategy':raw.get('strategy'),'from_stage':raw.get('from_stage')},
                'provenance':{'sources':[ref] if ref else []},'source_handoff_id':hid,
                'episode_trace_id':session,'episode_status':e['status'],
                'lineage':{'design':hid,'platform':platform,'project':p.name,'session':session}}
            if kind=='summary':u['episode_context']=trace
            units.append(u)
    if len({u['unit_id'] for u in units})!=len(units):raise ValueError('duplicate raw unit identity')
    return units,inputs


def step_overrides(recs,project,manifest):
    """Same strict admission as collection/LC; no dependency on their dispatcher."""
    import t2a_envelope as E
    from ann_v2 import fields as FD
    out={}
    for r in recs:
        if r.get('admission',{}).get('status')!='ADMITTED':continue
        tag=r.get('binding',{}).get('post',{}).get('run')
        run=project/'backend'/str(tag) if tag else None
        if run is None or not run.is_dir():
            out[r['unit_id']]={'admitted':False,'reason':'post run missing'};continue
        area=E.check_area(manifest,E.area_metrics(run))
        if not area['ok']:
            out[r['unit_id']]={'admitted':False,'reason':'area envelope not PASS','area':area,
                'outcome_class':'cleared_new_failure' if area.get('violations') else 'cleared_unconfirmed'};continue
        measured=FD.measure(run)
        new=[k for k,v in (r.get('new_failures') or {}).items() if v=='NOT_MEASURED_BEFORE'
             and measured.get(k,{}).get('measured') and measured.get(k,{}).get('present')]
        if new:out[r['unit_id']]={'admitted':False,'outcome_class':'cleared_new_failure','reason':','.join(new)}
    return out


def build_fold(root,nb,hid,all_units,registry,input_hashes):
    from ann_v2 import annotate as AN,verify as VF
    from tehm import neutral_export as NX
    from tehm.shared_import import digest
    from tehm.snapshot import seal_store
    from tehm_v2 import evidence_index as EI,policy as POL
    import t2_shared_history as SH
    import t2a_adapters as A
    import t2a_envelope as E
    import t2a_batch as B
    import t2_tehm_import_fix as X
    started,cpu=time.time(),time.process_time();fold=root/'folds'/hid
    if fold.exists():raise ValueError('no overwrite of a fold')
    fold.mkdir(parents=True)
    keep,drop=N.filter_history(all_units,hid,registry)
    if any(u['source_handoff_id']==hid for u in keep):raise ValueError('target leakage')
    allowed_origins={u['source_handoff_id'] for u in keep}
    # Filter source hashes too: no current-target NB trajectory in a builder manifest.
    allowed_projects={str(nb/'episodes'/(registry[x]['task'].replace('/','__')+'__NB__r1')) for x in allowed_origins}
    inputs={p:h for p,h in input_hashes.items() if any(Path(p).is_relative_to(Path(d)) for d in allowed_projects)}
    source=fold/'source';source.mkdir()
    (source/'source_export.jsonl').write_text(''.join(json.dumps(u,sort_keys=True)+'\n' for u in keep))
    save(source/'source_manifest.json',{'split':{'family_of_design':{x:registry[x]['verified_ip_key'] for x in allowed_origins}},
        'units':len(keep),'inputs':inputs,'status':'NB_ONLY_FILTERED_BEFORE_CONSTRUCTION'})
    save(fold/'exclusions.json',{'target':hid,'excluded_unit_ids':[u['unit_id'] for u in drop],
        'kept_unit_ids':[u['unit_id'] for u in keep],'excluded_origins':sorted({u['source_handoff_id'] for u in drop}),
        'kept_origins':sorted(allowed_origins),'policy':'reviewed same-IP only; before construction'})
    AN.main(['--source',str(source),'--out',str(fold/'ann')])
    ann=fold/'ann/ann_v2_dev.jsonl';rep=VF.verify(ann,source)
    save(fold/'verification.json',rep)
    if rep['gate']!='PASS' or rep['critical']:raise ValueError('independent evidence gate failed: '+hid)
    recs=rows(ann);overrides={}
    # Retain the collection's strict step admission, including area and later new failures.
    for x in allowed_origins:
        p=nb/'episodes'/(registry[x]['task'].replace('/','__')+'__NB__r1')
        ids={u['unit_id'] for u in keep if u['source_handoff_id']==x}
        man=load(p/'manifest.json')
        # check_area resolves the initial reference from the already frozen NB receipt.
        overrides.update(step_overrides([r for r in recs if r['unit_id'] in ids],p,man))
    save(fold/'overrides.json',overrides)
    units,man=SH.build([source/'source_export.jsonl'],[ann],{},'t2',overrides)
    package=fold/'shared';package.mkdir()
    (package/'shared_units.jsonl').write_text(''.join(json.dumps(u,sort_keys=True)+'\n' for u in units))
    man.update(units_sha256=sha(package/'shared_units.jsonl'))
    man['inputs'].update({str(fold/'overrides.json'):sha(fold/'overrides.json'),**inputs,
        str(source/'source_manifest.json'):sha(source/'source_manifest.json')})
    save(package/'manifest.json',man)
    reconstructed,_=SH.build([source/'source_export.jsonl'],[ann],{},'t2',overrides)
    if reconstructed!=units:raise ValueError('non-reproducible history')
    export=fold/'tehm-import';export.mkdir();view=SH.tehm_view(units)
    authority=export/'authority.json'
    files={**man['inputs'],str(package/'shared_units.jsonl'):man['units_sha256'],str(package/'manifest.json'):sha(package/'manifest.json'),
           str(fold/'verification.json'):sha(fold/'verification.json')}
    save(authority,{'schema':'tehm-shared-import-authority-v1','files':files,
        'verifier_receipts':[str(fold/'verification.json')],'units':{u['unit_id']:digest(u) for u in view}})
    for u in view:u['shared_evidence_proof']={'authority':str(authority),'authority_sha256':sha(authority)}
    (export/'source_export.jsonl').write_text(''.join(json.dumps(u,sort_keys=True)+'\n' for u in view))
    save(export/'source_manifest.json',{'export_sha256':sha(export/'source_export.jsonl'),'shared_units_sha256':man['units_sha256']})
    X.build_store(export,fold/'store',root/'code');seal_store(fold/'store')
    idx=EI.build_shared(units,[ann],'t2');idx['package']={'units_sha256':man['units_sha256']}
    save(fold/'evidence-index.json',idx)
    save(fold/'tehm-v2-policy.json',{'schema':'tehm-v2-policy-v1','mechanisms':{m:True for m in POL.MECHANISMS},
        'index':str(fold/'evidence-index.json'),'index_sha256':sha(fold/'evidence-index.json'),
        'task_ip_family':registry[hid]['verified_ip_key']})
    controls=fold/'controls';controls.mkdir()
    shutil.copytree(root/'code',controls/'code')
    base={'status':'FROZEN','source_sha256':man['units_sha256'],'units_sha256':man['units_sha256']}
    for arm in ('RET','RECIPE'):
        t0,c0=time.time(),time.process_time()
        content={'trajectories':A.trajectories(units),'model':'all-mpnet-base-v2'} if arm=='RET' else {'recipes':A.build_recipes(units),'match_keys':A.RECIPE_KEYS}
        save(controls/(arm+'.json'),{**base,**content,'construction_wall_s':time.time()-t0,'construction_cpu_s':time.process_time()-c0,
                                  'model_requests':0,'tokens':0,'EDA_flows':0})
    save(controls/'budget.json',{'budget_id':'nb8-construction-'+hid,'caps':{},'reserve':{},'halt_on_unknown_usage':True})
    (controls/'phase').mkdir();(controls/'phase/resource_ledger.jsonl').write_bytes(b'')
    order=[u['unit_id'] for u in sorted(units,key=lambda u:(u['identity'].get('ts') or '',u['unit_id']))]
    save(controls/'registration.json',{'code':{str(Path(p).relative_to(controls)):h for p,h in frozen(controls/'code').items()},
        'shared_package':str(package),'shared_manifest_sha256':sha(package/'manifest.json'),
        'shared_units_sha256':man['units_sha256'],'amem_order':order,'amem_order_sha256':A.fingerprint(order),
        'native_parse_fallback':True,'input_bytes_ceiling':96000,'output_tokens_ceiling':2000,'transport_retries':0})
    # Runtime is exactly the baseline copy, apart from the registered history utilities.
    shutil.copytree(root/'code',fold/'code')
    task=registry[hid]['task'];src=Path(next(t['template'] for t in load(nb/'registration.json')['tasks'] if t['task']==task))
    dst=fold/'tasks'/src.name;shutil.copytree(src,dst)
    cfg=dst/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(src)+'/',str(dst)+'/'))
    m=load(dst/'manifest.json');ref=load(nb/'episodes'/(task.replace('/','__')+'__NB__r1')/'reports/initial-area-reference.json')
    for k in N.CAPS:m['area'][k]['ref']=ref['areas'][k];m['area'][k]['ref_stage']='initial_2_1_floorplan'
    m.pop('area_reference_policy',None);m['source_project']=str(dst);m['initial_config']=E.effective_config(cfg.read_text());m['initial_config_sha256']=sha(cfg)
    save(dst/'manifest.json',m)
    save(fold/'episode-budget.json',B.episode_budget())
    shutil.copy2(nb/'online-input-authorization.json',fold/'online-input-authorization.json')
    save(fold/'offline-ready.json',{'status':'READY','units':len(units),'admitted':man['admitted'],
         'target':hid,'excluded':sorted({u['source_handoff_id'] for u in drop}),
         'source_units_sha256':man['units_sha256'],'no_paid_calls_yet':True,
         'preparation_wall_s':time.time()-started,'preparation_cpu_s':time.process_time()-cpu})
    return fold


def ledger_cost(path):
    ev=rows(path);req=[v for v in ev if v.get('type')=='llm_request'];tokens=unknown=0
    for r in req:
        u=r.get('usage_reported')
        if isinstance(u,dict) and all(isinstance(u.get(k),(int,float)) for k in ('prompt_tokens','completion_tokens')):
            tokens+=u['prompt_tokens']+u['completion_tokens']
        else:unknown+=1
    metrics=rows(Path(path).with_name('embedding-calls.jsonl'))
    checkers=[v for v in ev if v.get('type')=='checker_run']
    log=Path(path).parent.parent/'eval_arm.log'
    observed=sum(line.strip() in ('[strict-signoff] drc','[strict-signoff] lvs','[strict-signoff] rcx')
                 for line in log.read_text(errors='replace').splitlines()) if log.exists() else 0
    strict_events=sum(v.get('source')=='strict_signoff' for v in checkers)
    strict_calls=max(observed,strict_events)
    checker_calls=strict_calls+sum(v.get('source')!='strict_signoff' for v in checkers)
    orchestrator_calls=sum(v.get('type')=='tool_call' for v in ev)
    return {'requests':len(req),'known_tokens':tokens,'unknown_requests':unknown,
            'embedding_operations':len(metrics),'embedding_cpu_s':sum(v.get('cpu_s',0) for v in metrics),
            'embedding_wall_s':sum(v.get('wall_s',0) for v in metrics),
            'flows':sum(v.get('type')=='budget_charge' and v.get('resource')=='flows' for v in ev),
            'tool_calls':orchestrator_calls+checker_calls,'checker_runs':checker_calls,
            'tool_calls_orchestrator':orchestrator_calls,
            'legacy_strict_checker_calls_reconstructed':max(0,observed-strict_events)}


def collect(root):
    q=load(root/'queue-registration.json');nb=Path(q['nb']);registry=load(nb/'origin-registry.json');episodes=[];table=[]
    for hid in N.IDS:
        task=registry[hid]['task'];matrix={'design':hid,'task':task}
        for arm in ('NB',*ARMS):
            base=nb if arm=='NB' else root/'folds'/hid
            p=base/'episodes'/(task.replace('/','__')+'__'+arm+'__r1');file=p/'episode.json'
            e=load(file) if file.exists() else {};status=e.get('status','NOT_STARTED');matrix[arm]=status
            episodes.append({'design':hid,'task':task,'arm':arm,'status':status,'episode':str(file),
                'wall_s':e.get('total_worker_wall_s'),'resources':e.get('resources'),
                'accounting':e.get('accounting'),'token_usage':e.get('token_usage'),
                'cost':ledger_cost(p/'reports/resource_ledger.jsonl') if file.exists() else None})
        table.append(matrix)
    summaries={}
    for arm in ('NB',*ARMS):
        rr=[e for e in episodes if e['arm']==arm];costs=[r['cost'] for r in rr if r['cost'] is not None]
        valid=[r for r in rr if r['status'] in ('ACCEPTED','NOT_ACCEPTED')]
        paired=[]
        for r in valid:
            n=next(x for x in episodes if x['design']==r['design'] and x['arm']=='NB')
            if n['status'] in ('ACCEPTED','NOT_ACCEPTED'):paired.append((r,n))
        summaries[arm]={'registered':8,'measured_complete':len(valid),'accepted':sum(r['status']=='ACCEPTED' for r in rr),
            'status_counts':{s:sum(r['status']==s for r in rr) for s in sorted({r['status'] for r in rr})},
            'cost_totals_so_far':{k:sum(c[k] for c in costs) for k in ('requests','known_tokens','unknown_requests','flows','tool_calls','checker_runs','embedding_operations','embedding_cpu_s','embedding_wall_s')},
            'measured_pairs':len(paired),'G':sum(r['status']=='ACCEPTED' and n['status']=='NOT_ACCEPTED' for r,n in paired),
            'H':sum(r['status']=='NOT_ACCEPTED' and n['status']=='ACCEPTED' for r,n in paired)}
    construction={}
    for hid in N.IDS:
        fold=root/'folds'/hid
        for arm in ('EXPEL','AWM','AMEM'):
            construction[hid+'/'+arm]=ledger_cost(fold/'controls/construction'/arm/'reports/resource_ledger.jsonl')
    save(root/'table2-results.json',{'updated':time.time(),'registered_episodes':56,'NB_reused_not_rerun':True,
        'episodes':episodes,'arms':summaries,'construction_costs_separate':construction,
        'complete':all(e['status'] in ('ACCEPTED','NOT_ACCEPTED') for e in episodes)})
    with (root/'table2-matrix.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=['design','task','NB',*ARMS]);w.writeheader();w.writerows(table)


def seal_runtime(root,fold,nb,registry,hid):
    import t2a_batch as B
    for arm in ARMS[:-1]:
        data=load(fold/'controls'/(arm+'.json'))
        if data.get('status')!='FROZEN' or data.get('units_sha256')!=load(fold/'shared/manifest.json')['units_sha256']:
            raise ValueError('arm history identity mismatch: '+arm)
    reg0=load(nb/'registration.json');task=registry[hid]['task'];template=next((fold/'tasks').iterdir())
    binding={'budget_file':str(root/'online-budget.json'),'budget_sha256':sha(root/'online-budget.json'),
        'ledger_path':str(root/'online-phase/resource_ledger.jsonl'),'prior_ledger_bytes':0,
        'prior_ledger_sha256':hashlib.sha256(b'').hexdigest()}
    files={}
    for sub in ('code','store','shared','tehm-import','source','ann','tasks'):files.update(frozen(fold/sub))
    for arm in ARMS[:-1]:files[str(fold/'controls'/(arm+'.json'))]=sha(fold/'controls'/(arm+'.json'))
    for n in ['tehm-v2-policy.json','evidence-index.json','episode-budget.json','online-input-authorization.json','verification.json','overrides.json','exclusions.json']:
        files[str(fold/n)]=sha(fold/n)
    files.update(reg0['external_hashes']);files.update(load(root/'queue-registration.json')['external_inputs'])
    m=load(template/'manifest.json')
    files.update({p:h.removeprefix('sha256:') for p,h in m['immutable']['files'].items() if Path(p).is_absolute()})
    for n in ('online-budget.json','authorization.json'):files[str(root/n)]=sha(root/n)
    reg={'schema':'nb-firstlook8-memory-v1','status':'FROZEN_READY','split':'TEST','paper_data':False,
        'tasks':[{'task':task,'template':str(template),'handoff_id':hid}],'arms':list(ARMS),'repeats':1,
        'plan':[{'task':task,'arm':a,'repeat':1} for a in ARMS],'files':files,
        'online_policy':B.T.online_policy(),'phase_binding':binding,'mechanism_policy':None,
        'history_policy':'NB_ONLY_LEAVE_ONE_IP_OUT_BEFORE_CONSTRUCTION','runtime_base':str(nb),
        'source_units_sha256':load(fold/'shared/manifest.json')['units_sha256'],'launch_blockers':[]}
    save(fold/'registration.json',reg);B.preflight(fold,'TEST')


def dispatch(root,jobs,popen=None,sleep=time.sleep,terminal_policy=None):
    """Seven slots; never retry completed/partial episodes. Drain on a technical fault."""
    popen=popen or subprocess.Popen;pending=list(jobs);live={};done=[];reason=None
    while pending or live:
        for i,slot in enumerate(SLOTS):
            if reason or i in live or not pending:continue
            row=pending.pop(0);fold=Path(row['root']);name=row['task'].replace('/','__')+'__'+row['arm']+'__r1'
            if (fold/'episodes'/name).exists():raise ValueError('episode already exists; no implicit retry')
            log=(root/'logs'/(name+'.log')).open('x')
            cmd=['taskset','-c',','.join(map(str,slot)),sys.executable,str(fold/'code/memory/scripts/t2a_batch.py'),
                'episode','--root',str(fold),'--task',row['task'],'--arm',row['arm'],'--repeat','1','--split','TEST']
            p=popen(cmd,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'',
                'R2G_EMBEDDING_METRICS_FILE':str(fold/'episodes'/name/'reports/embedding-calls.jsonl')},
                stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            live[i]=(p,log,row,name)
        for i,(p,log,row,name) in list(live.items()):
            if p.poll() is None:continue
            log.close();file=Path(row['root'])/'episodes'/name/'episode.json';e=load(file) if file.exists() else {}
            valid_terminal=(e.get('status') in ('ACCEPTED','NOT_ACCEPTED') or
                            (terminal_policy is not None and terminal_policy(file.parent,e)))
            if p.returncode or not valid_terminal or not e.get('accounting',{}).get('passed'):
                reason='technical/accounting fault: '+name
            done.append({**row,'status':e.get('status'),'returncode':p.returncode});del live[i]
        save(root/'dispatch-state.json',{'status':'PAUSED_DRAINING' if reason and live else 'PAUSED' if reason else 'RUNNING',
            'done':done,'running':[{'pid':p.pid,**row} for p,log,row,n in live.values()],
            'pending':len(pending),'reason':reason,'updated':time.time()})
        if (root/'queue-registration.json').exists():collect(root)
        if reason and not live:break
        if pending or live:sleep(30)
    result={'status':'PAUSED' if reason else 'COMPLETED','done':done,'running':[],'pending':len(pending),'reason':reason,'ended':time.time()}
    save(root/'dispatch-state.json',result)
    if (root/'queue-registration.json').exists():collect(root)
    return result


def prepare(root,nb):
    import t2a_batch as B
    import t2a_adapters as A
    if root.exists():raise ValueError('followup registration already exists')
    B.preflight(nb,'TEST');root.mkdir(parents=True)
    shutil.copytree(nb/'code',root/'code')
    # The historical DEV copier pruned every directory named "evaluation",
    # including this import-time runtime package. Restore code only from the
    # existing frozen LC runtime; never read any LC experience/store/results.
    evaluation=N.EVAL/'t2-lc-v1-20261018c/code/memory/tehm/evaluation'
    shutil.copytree(evaluation,root/'code/memory/tehm/evaluation',
                    ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in ('t2_nb_followup.py','t2a_construct.py','t2a_adapters.py','t2_shared_history.py','t2_embedding_broker.py'):
        shutil.copy2(Path(__file__).parent/name,root/'code/memory/scripts'/name)
    upstream=frozen(evaluation)
    for name in UPSTREAM:
        src=A.ROOT/name;dst=root/'code/upstream'/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        upstream[str(src)]=sha(src)
    emb=A.ROOT/'embedding-models/verified_embedding_receipt.json';er=load(emb)
    for model,details in er['models'].items():
        for rel,meta in details['files'].items():
            file=emb.parent/model/rel
            if sha(file)!=meta['sha256']:raise ValueError('embedding differs from verified model')
            upstream[str(file)]=meta['sha256']
    upstream[str(emb)]=sha(emb)
    upstream[str(EMBEDDING_PYTHON)]=sha(EMBEDDING_PYTHON)
    save(root/'authorization.json',{'user_text':'NB基线完成后自动开始七臂并发运行的任务',
        'scope':'reuse eight completed NB cells, construct per-target memories from other NB designs, run 48 memory cells with at most seven workers',
        'provider':'official https://api.deepseek.com/chat/completions; deepseek-flash',
        'construction_input':'public NB actions, outcomes, costs and provenance; NO RTL/SDC/API key/current-target trajectory',
        'cumulative_caps':None,'repair_generations':3,'input_bytes_ceiling':96000,'transport_retries':0,
        'learning_curve':'unchanged','no_NB_rerun':True})
    save(root/'online-budget.json',B.formal_phase_budget()|{'budget_id':'nb8-memory-online'})
    (root/'online-phase').mkdir();(root/'online-phase/resource_ledger.jsonl').write_bytes(b'')
    (root/'logs').mkdir()
    files=frozen(root/'code');files.update({str(root/n):sha(root/n) for n in ('authorization.json','online-budget.json')})
    save(root/'queue-registration.json',{'schema':'nb8-followup-queue-v1','nb':str(nb),'nb_registration_sha256':sha(nb/'registration.json'),
        'origin_registry_sha256':sha(nb/'origin-registry.json'),'files':files,'external_inputs':upstream,
        'embedding_python':str(EMBEDDING_PYTHON),'embedding_library_path':str(EMBEDDING_LIB),
        'arms':list(ARMS),'plan_size':48,'concurrency':7,'cpu_slots':SLOTS,'poll_s':30})
    save(root/'dispatch-state.json',{'status':'ARMED_WAITING_FOR_NB','pending':48,'done':[],'running':[]})


def run(root):
    import t2a_batch as B
    q=load(root/'queue-registration.json');nb=Path(q['nb'])
    os.environ.update(R2G_EMBEDDING_PYTHON=q['embedding_python'],R2G_EMBEDDING_LIBRARY_PATH=q['embedding_library_path'])
    if Path(__file__).resolve()!=root/'code/memory/scripts/t2_nb_followup.py':raise ValueError('private frozen controller required')
    lock=(root/'supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    verify_files(q['files']);verify_files(q['external_inputs'])
    if sha(nb/'registration.json')!=q['nb_registration_sha256'] or sha(nb/'origin-registry.json')!=q['origin_registry_sha256']:
        raise ValueError('NB registration/origin changed')
    cpus=[c for s in SLOTS for c in s]
    if len(cpus)!=len(set(cpus)) or not set(cpus)<=os.sched_getaffinity(0):raise ValueError('CPU slots unavailable')
    with (root/'controller-start.json').open('x') as f:json.dump({'started':time.time(),'registration_sha256':sha(root/'queue-registration.json')},f)
    try:
        while not nb_gate(nb):
            save(root/'dispatch-state.json',{'status':'ARMED_WAITING_FOR_NB','pending':48,'done':[],'running':[],'updated':time.time()})
            time.sleep(q['poll_s'])
        B.preflight(nb,'TEST')
        registry=load(nb/'origin-registry.json');units,inputs=raw_units(nb,registry)
        collect(root)
        save(root/'nb-terminal-receipt.json',{'nb_state_sha256':sha(nb/'dispatch-state.json'),'raw_inputs':inputs,'units':len(units)})
        folds=[]
        for hid in N.IDS:
            save(root/'dispatch-state.json',{'status':'OFFLINE_HISTORY_AND_STORES','target':hid,'pending':48,'done':[],'running':[]})
            folds.append(build_fold(root,nb,hid,units,registry,inputs))
        # Finish every offline gate before the first paid construction request.
        for fold in folds:
            for arm in ('EXPEL','AWM','AMEM'):
                save(root/'dispatch-state.json',{'status':'BUILDING_MEMORY','target':fold.name,'arm':arm,'pending':48,'done':[],'running':[]})
                cmd=[sys.executable,str(root/'code/memory/scripts/t2a_construct.py'),'build','--root',str(fold/'controls'),'--arm',arm]
                with (root/'logs'/('build-'+fold.name+'-'+arm+'.log')).open('x') as log:
                    subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT,check=True,env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'',
                        'R2G_EMBEDDING_METRICS_FILE':str(fold/'controls/construction'/arm/'reports/embedding-calls.jsonl')})
            seal_runtime(root,fold,nb,registry,fold.name)
        jobs=[{'root':str(fold),'task':registry[fold.name]['task'],'arm':arm} for fold in folds for arm in ARMS]
        save(root/'frozen-memory-plan.json',{'jobs':jobs,'registrations':{str(f/'registration.json'):sha(f/'registration.json') for f in folds},'NB_reused':8})
        dispatch(root,jobs)
    except Exception as exc:
        save(root/'dispatch-state.json',{'status':'PAUSED_TECHNICAL','reason':str(exc),'updated':time.time()})
        raise


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('prepare','run'))
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--nb',type=Path)
    a=ap.parse_args();root=a.root.resolve()
    if a.command=='prepare':prepare(root,a.nb.resolve())
    else:run(root)

if __name__=='__main__':main()
