#!/usr/bin/env python3
"""Frozen automatic NB -> eight target-excluded memories -> 64 parallel cells.

The old six-arm controller remains unchanged. Shared history import, annotation,
isolation and accounting primitives are reused; this version owns its arm set,
runtime integration, 3-total-call policy and construction sources explicitly.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

REPO=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(REPO/'memory/scripts'),str(REPO/'memory')]
import t2_nb_followup as F
import t2_agent_dispatch as D
from t2_agent_nb_package import replace_once
from t2_agent_new_methods import CONFIG,REME_SHA,RB_SHA
from t2_agent_memories import TM0_POLICY

ARMS=('RET','RECIPE','EXPEL','AWM','AMEM','REME','REASONINGBANK','TM0')
BUILDS=('EXPEL','AWM','AMEM','REME','REASONINGBANK')
SLOTS=[list(range(lo,lo+8))+list(range(lo+96,lo+104)) for lo in (0,10,20,30,40,50,60,84)]
SCRIPTS=('t2_agent_followup.py','t2_agent_memories.py','t2_agent_new_methods.py',
         't2_agent_runtime.py','t2_agent_dispatch.py','t2_agent_nb_package.py',
         't2_agent_tools.py','t2_agent_verdict.py','t2_agent_journal.py',
         't2_nb_followup.py','t2a_construct.py','t2a_adapters.py','t2_shared_history.py',
         't2_embedding_broker.py','t2_tehm_agent_memory.py')
sha,load,save=F.sha,F.load,F.save


def integrate_worker(text):
    text=replace_once(text,"        if arm != 'NB':raise RuntimeError('This first stage registers NB only')\n",'')
    text=replace_once(text,"        memory=A.PromptMemory(arm,root/'controls') if arm in PROMPT_ARMS else None",
        "        from t2_agent_memories import make_memory\n"
        "        memory=make_memory(H,project,arm,root,env)")
    text=replace_once(text,"bind_snapshot(root/'store',project/'.tehm-store')",
        "bind_snapshot(root/'agent-store',project/'.tehm-store')")
    text=replace_once(text,'run_agent(H,project,arm,env,key,initial,mark_engineer_restore)',
        'run_agent(H,project,arm,env,key,initial,mark_engineer_restore,memory=memory)')
    return text


def configure_reuse():
    # Explicit version-local parameters for the reused collector/dispatcher.
    F.ARMS=ARMS;F.SLOTS=SLOTS;F.collect=collect


def nb_gate(nb):
    reg=load(nb/'registration.json')
    if reg.get('agent_selected_policy')!=D.VERSION or reg.get('online_total_request_cap')!=3:
        raise ValueError('Cannot reuse deterministic-first NB')
    if not F.nb_gate(nb):return False
    for p in (nb/'episodes').glob('*/episode.json'):
        if load(p)['accounting']['llm_requests']>3:raise ValueError('NB online opportunity violation')
    return True


def collect(root):
    # Preserve old all-episode totals/denominators but correct the new scope.
    original=_OLD_COLLECT
    original(root)
    data=load(root/'table2-results.json');data['registered_episodes']=72
    data['protocol']=D.VERSION;data['native_configuration']=CONFIG
    for arm,summary in data['arms'].items():
        rows=[r for r in data['episodes'] if r['arm']==arm]
        summary['mean_cost_per_registered_episode_so_far']={k:v/8 for k,v in summary['cost_totals_so_far'].items()}
        summary['worker_wall_s_total_so_far']=sum(r['wall_s'] or 0 for r in rows)
        summary['mean_worker_wall_s_per_registered_episode_so_far']=summary['worker_wall_s_total_so_far']/8
        summary['means_are_final']=summary['measured_complete']==8
    for hid in F.N.IDS:
        fold=root/'folds'/hid
        for arm in BUILDS:
            data['construction_costs_separate'][hid+'/'+arm]=F.ledger_cost(fold/'controls/construction'/arm/'reports/resource_ledger.jsonl')
        for arm,file in [('TM0',fold/'agent-store/agent-construction.json'),
                         ('RET',fold/'controls/RET.json'),('RECIPE',fold/'controls/RECIPE.json')]:
            if file.exists():
                d=load(file)
                data['construction_costs_separate'][hid+'/'+arm]={'requests':0,'known_tokens':0,
                    'wall_s':d.get('wall_s',d.get('construction_wall_s')),
                    'cpu_s':d.get('cpu_s',d.get('construction_cpu_s'))}
    save(root/'table2-results.json',data)


_OLD_COLLECT=F.collect


def upstream_sources():
    reme=Path('/data1/zhangdy/tehm-integration-external/ReMe-v0.2.0.6')
    rb=Path('/data1/zhangdy/tehm-integration-external/reasoning-bank-20261021')
    for path,want in ((reme,REME_SHA),(rb,RB_SHA)):
        actual=subprocess.check_output(['git','-C',str(path),'rev-parse','HEAD'],text=True).strip()
        if actual!=want:raise ValueError('Upstream pin changed: '+str(path))
        if subprocess.check_output(['git','-C',str(path),'status','--porcelain'],text=True).strip():
            raise ValueError('Dirty upstream reference')
    result=[]
    for p in sorted((reme/'reme_ai/summary/task').glob('*')):
        if p.is_file():result.append((p,Path('ReMe')/p.relative_to(reme)))
    for p in sorted((reme/'reme_ai/retrieve/task').glob('*')):
        if p.is_file():result.append((p,Path('ReMe')/p.relative_to(reme)))
    for rel in ('LICENSE','reme_ai/config/default.yaml'):
        result.append((reme/rel,Path('ReMe')/rel))
    for rel in ('LICENSE','third_party/src/minisweagent/memory/instruction.py',
                'third_party/src/minisweagent/memory/induce_memory.py',
                'third_party/src/minisweagent/memory/memory_management.py',
                'third_party/src/minisweagent/run/extra/swebench.py',
                'third_party/src/minisweagent/agents/default.py'):
        result.append((rb/rel,Path('ReasoningBank')/rel))
    return result


def prepare(root,nb,tests):
    import t2a_adapters as A
    cases=list(ET.parse(tests).getroot().iter('testcase'))
    if len(cases)<35 or any(c.find(tag) is not None for c in cases for tag in ('failure','error','skipped')):
        raise ValueError('Passing agent adaptation/queue tests required')
    reg=load(nb/'registration.json')
    if reg.get('agent_selected_policy')!=D.VERSION:raise ValueError('Wrong NB version')
    F.verify_files(reg['files'])
    if root.exists():raise ValueError('No overwrite of automatic queue')
    root.mkdir(parents=True);shutil.copytree(nb/'code',root/'code')
    evaluation=F.N.EVAL/'t2-lc-v1-20261018c/code/memory/tehm/evaluation'
    if not (root/'code/memory/tehm/evaluation').exists():
        shutil.copytree(evaluation,root/'code/memory/tehm/evaluation',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    for name in SCRIPTS:shutil.copy2(REPO/'memory/scripts'/name,root/'code/memory/scripts'/name)
    # Use the current evidence-preserving native consumer dependencies; no new
    # executable knowledge or admission weakening. Hash the explicit delta.
    for rel in ('memory/tehm/adapters/r2g_evidence.py','memory/tehm/causal/mechanism.py',
                'memory/tehm/evidence.py','memory/tehm/neutral_export.py','memory/tehm_backend.py'):
        shutil.copy2(REPO/rel,root/'code'/rel)
    batch=root/'code/memory/scripts/t2a_batch.py';batch.write_text(integrate_worker(batch.read_text()))
    runtime=root/'code/memory/scripts/t2a_runtime.py'
    runtime.write_text(replace_once(runtime.read_text(),
        "ARMS = ('NB', 'TM0', 'RET', 'RECIPE', 'EXPEL', 'AWM', 'AMEM')",
        'ARMS = '+repr(D.ARMS)))
    upstream=F.frozen(evaluation)
    sources=[(A.ROOT/name,Path(name)) for name in F.UPSTREAM]+upstream_sources()
    for src,rel in sources:
        dst=root/'code/upstream'/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        upstream[str(src)]=sha(src)
    emb=A.ROOT/'embedding-models/verified_embedding_receipt.json'
    for model,details in load(emb)['models'].items():
        for rel,meta in details['files'].items():
            file=emb.parent/model/rel
            if sha(file)!=meta['sha256']:raise ValueError('Embedding pin changed')
            upstream[str(file)]=meta['sha256']
    upstream[str(emb)]=sha(emb);upstream[str(F.EMBEDDING_PYTHON)]=sha(F.EMBEDDING_PYTHON)
    shutil.copy2(tests,root/'offline-tests.xml')
    save(root/'authorization.json',{'user_text':'同步对这两种方法做一下适配NB跑完就并发启动其他实验臂',
        'online_confirmation':'全部在线请求合计最多 3 次',
        'scope':'new NB8 -> held-out target/same-IP excluded history -> eight memory arms, 64 episodes',
        'provider':'https://api.deepseek.com/chat/completions','model':'deepseek-flash',
        'construction_input':'eligible public NB actions/outcomes/rollback/cost/context only; no TEST RTL, SDC or key',
        'cumulative_caps':None,'total_online_requests_per_episode':3,'input_bytes_ceiling':96000,
        'implicit_retries':0,'UNKNOWN':'pause admission','learning_curve':'unchanged','prior_results':'unchanged'})
    save(root/'online-budget.json',{'budget_id':'agent9-memory-online','caps':{},'reserve':{},'halt_on_unknown_usage':True})
    (root/'online-phase').mkdir();(root/'online-phase/resource_ledger.jsonl').touch(exist_ok=False)
    save(root/'construction-budget.json',{'budget_id':'agent9-memory-construction','caps':{},'reserve':{},'halt_on_unknown_usage':True})
    (root/'construction-phase').mkdir();(root/'construction-phase/resource_ledger.jsonl').touch(exist_ok=False)
    (root/'logs').mkdir()
    files=F.frozen(root/'code')
    for n in ('authorization.json','online-budget.json','construction-budget.json','offline-tests.xml'):files[str(root/n)]=sha(root/n)
    save(root/'queue-registration.json',{'schema':'agent9-followup-v1','nb':str(nb),
        'nb_registration_sha256':sha(nb/'registration.json'),'origin_registry_sha256':sha(nb/'origin-registry.json'),
        'files':files,'external_inputs':upstream,'embedding_python':str(F.EMBEDDING_PYTHON),
        'embedding_library_path':str(F.EMBEDDING_LIB),'arms':list(ARMS),'plan_size':64,'concurrency':8,
        'cpu_slots':SLOTS,'poll_s':30,'construction_concurrency':4,'method_configs':CONFIG,'runtime_policy':D.VERSION,
        'tehm_consumer_policy':TM0_POLICY,
        'validation_boundary':'offline technical tests; no new paid DEV/performance gate'})
    save(root/'dispatch-state.json',{'status':'ARMED_WAITING_FOR_NB','pending':64,'done':[],'running':[]})


def build_fold(root,nb,hid,units,registry,inputs):
    from t2_tehm_agent_memory import enrich_store
    fold=F.build_fold(root,nb,hid,units,registry,inputs)
    save(fold/'episode-budget.json',D.episode_budget())
    enrich_store(fold,fold/'agent-store')
    reg=load(fold/'controls/registration.json')
    reg['phase_binding']={'budget_file':str(root/'construction-budget.json'),
        'budget_sha256':sha(root/'construction-budget.json'),
        'ledger_path':str(root/'construction-phase/resource_ledger.jsonl')}
    save(fold/'controls/registration.json',reg)
    return fold


def construct_all(root,folds,popen=subprocess.Popen,sleep=time.sleep):
    pending=[(f,a) for f in folds for a in BUILDS];live=[];done=[];failure=None
    while pending or live:
        while pending and len(live)<4 and not failure:
            fold,arm=pending.pop(0)
            script='t2_agent_new_methods.py' if arm in CONFIG else 't2a_construct.py'
            cmd=[sys.executable,str(root/'code/memory/scripts'/script)]
            if arm not in CONFIG:cmd+=['build']
            cmd+=['--root',str(fold/'controls'),'--arm',arm]
            log=(root/'logs'/('build-'+fold.name+'-'+arm+'.log')).open('x')
            p=popen(cmd,stdout=log,stderr=subprocess.STDOUT,env={**os.environ,
                'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'',
                'R2G_EMBEDDING_METRICS_FILE':str(fold/'controls/construction'/arm/'reports/embedding-calls.jsonl')})
            live.append((p,log,fold,arm))
        for p,log,fold,arm in list(live):
            if p.poll() is None:continue
            log.close();live.remove((p,log,fold,arm))
            done.append({'target':fold.name,'arm':arm,'returncode':p.returncode})
            if p.returncode:failure=f'Construction failed: {fold.name}/{arm}; see preserved raw requests and ledger'
        save(root/'dispatch-state.json',{'status':'BUILDING_MEMORY' if not failure else 'PAUSED_DRAINING_BUILDERS',
            'construction_done':done,'construction_pending':len(pending),
            'running':[{'pid':p.pid,'target':f.name,'arm':a} for p,log,f,a in live],
            'pending':64,'reason':failure,'updated':time.time()})
        if failure and not live:raise RuntimeError(failure)
        if pending or live:sleep(5)
    save(root/'construction-completed.json',{'jobs':done,'implicit_retries':0})


def seal(root,fold,nb,registry):
    # F's seal checks every non-TM0 representation; TM0 is last in this version.
    F.seal_runtime(root,fold,nb,registry,fold.name)
    reg=load(fold/'registration.json')
    reg['files'].update(F.frozen(fold/'agent-store'))
    reg.update(agent_selected_policy=D.VERSION,total_online_requests_per_episode=3,
               tehm_consumer_policy=TM0_POLICY,
               method_configs=CONFIG,prior_results_not_overwritten=True)
    save(fold/'registration.json',reg)
    import t2a_batch as B
    B.preflight(fold,'TEST')


def run(root):
    configure_reuse()
    if Path(__file__).resolve()!=root/'code/memory/scripts/t2_agent_followup.py':
        raise ValueError('Use frozen private controller')
    q=load(root/'queue-registration.json');nb=Path(q['nb'])
    lock=(root/'supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    F.verify_files(q['files']);F.verify_files(q['external_inputs'])
    if sha(nb/'registration.json')!=q['nb_registration_sha256'] or sha(nb/'origin-registry.json')!=q['origin_registry_sha256']:
        raise ValueError('NB identity changed')
    os.environ.update(R2G_EMBEDDING_PYTHON=q['embedding_python'],R2G_EMBEDDING_LIBRARY_PATH=q['embedding_library_path'])
    cpus=[c for slot in SLOTS for c in slot]
    if len(cpus)!=len(set(cpus)) or not set(cpus)<=os.sched_getaffinity(0):raise ValueError('CPU slots unavailable')
    with (root/'controller-start.json').open('x') as f:json.dump({'started':time.time(),'registration_sha256':sha(root/'queue-registration.json')},f)
    try:
        while not nb_gate(nb):time.sleep(q['poll_s'])
        import t2a_batch as B
        B.preflight(nb,'TEST')
        registry=load(nb/'origin-registry.json');units,inputs=F.raw_units(nb,registry)
        # Include host decisions, tool selections and feedback, not just direct edits.
        for u in units:
            if 'episode_context' in u:
                p=nb/'episodes'/(registry[u['source_handoff_id']]['task'].replace('/','__')+'__NB__r1')
                u['episode_context']['agent_trajectory']=load(p/'episode.json').get('agent_run',{})
        save(root/'nb-terminal-receipt.json',{'state_sha256':sha(nb/'dispatch-state.json'),'raw_inputs':inputs,'units':len(units)})
        folds=[]
        for hid in F.N.IDS:
            save(root/'dispatch-state.json',{'status':'BUILDING_SHARED_HISTORY','target':hid,'pending':64,'running':[]})
            folds.append(build_fold(root,nb,hid,units,registry,inputs))
        construct_all(root,folds)
        for fold in folds:seal(root,fold,nb,registry)
        jobs=[{'root':str(fold),'task':registry[fold.name]['task'],'arm':arm} for fold in folds for arm in ARMS]
        save(root/'frozen-memory-plan.json',{'jobs':jobs,'registrations':{str(f/'registration.json'):sha(f/'registration.json') for f in folds}})
        F.dispatch(root,jobs)
    except Exception as exc:
        save(root/'dispatch-state.json',{'status':'PAUSED_TECHNICAL','reason':str(exc),'updated':time.time()})
        raise


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('prepare','run'))
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--nb',type=Path);ap.add_argument('--tests',type=Path)
    a=ap.parse_args()
    if a.command=='prepare':prepare(a.root.resolve(),a.nb.resolve(),a.tests)
    else:run(a.root.resolve())
