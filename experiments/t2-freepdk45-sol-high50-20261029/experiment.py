"""Frozen 50-task, four-lane Sol model baseline; no old experiment mutation."""
import argparse
import collections
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
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
BASE=ROOT.parent/'t2-freepdk45-main50-adamem-20261022-r3'
PYTHON='/data1/zhangdy/EDACode/.venv/bin/python'
sys.path[:0]=[str(ROOT),str(ROOT/'code/memory/scripts'),str(ROOT/'code/memory')]
import sol_provider as P
import sol_install as I


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def record(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n');temporary.replace(path)


def prepare():
    if (ROOT/'registration.json').exists():raise ValueError('Never replace a prepared registration')
    smoke=load(ROOT/'smoke/result.json')
    if not smoke.get('passed'):raise ValueError('Provider smoke did not pass')
    source=BASE/'nb/registration.json';reg=copy.deepcopy(load(source))
    if len(reg['plan'])!=50 or any(j['arm']!='NB' or j['repeat']!=1 for j in reg['plan']):
        raise ValueError('Expected the original fifty-task NB protocol')
    for name in ('episode-budget.json','online-input-authorization.json'):
        shutil.copy2(BASE/'nb'/name,ROOT/name)
    (ROOT/'phase').mkdir(exist_ok=True);(ROOT/'phase/resource_ledger.jsonl').touch(exist_ok=False)
    record(ROOT/'phase-budget.json',{'budget_id':'table2-sol-high50-20261029','caps':{},'reserve':{},
        'halt_on_unknown_usage':False,'actual_unknown_usage_not_zero':True})
    reg.update(status='PREPARED_NOT_FROZEN',namespace_arm='R2G_SOL_HIGH',arms=['NB'],
        display_arm='R2G (gpt-6.1-sol, high)',online_policy=I.policy(),
        phase_binding={'budget_file':str(ROOT/'phase-budget.json'),'budget_sha256':sha(ROOT/'phase-budget.json'),
                       'ledger_path':str(ROOT/'phase/resource_ledger.jsonl')},
        scheduler={**reg['scheduler'],'concurrency':4,'cpu_slots':load(BASE/'registration.json')['slots'][3:7]},
        launch_blockers=[],paper_data=True,model_baseline_extension=True,cross_task_memory=False,
        original_registration=str(source),original_registration_sha256=sha(source),
        online_entrypoint=str(ROOT/'worker.py'),
        validation_boundary='provider smoke plus offline ledger/executor equivalence tests; no new EDA DEV run',
        inference_scope='same already-exposed fifty designs, not an independent new TEST')
    reg['files'][str(source)]=sha(source)
    predecessor=ROOT.parent/'t2-freepdk45-terra-high50-20261029/registration.json'
    pause=ROOT/'terra-operator-pause.json'
    paused=load(pause)
    if paused.get('status')!='OPERATOR_PAUSED_FOR_MODEL_SWITCH':
        raise ValueError('Terra operator pause was not recorded')
    reg['files'][str(pause)]=sha(pause)
    reg['files'][str(predecessor)]=sha(predecessor)
    reg['model_switch']={'requested_target':'gpt-6.1-sol','reasoning_effort':'high',
        'predecessor_registration':str(predecessor),'pause_receipt':str(pause),
        'start_all_fifty_afresh':True,'reuse_predecessor_outcomes':False,
        'experimental_checker_corrections_included':False}
    record(ROOT/'registration-staged.json',reg)
    print(json.dumps({'tasks':len(reg['plan']),'model':P.MODEL,'reasoning_effort':'high','concurrency':4}))


def freeze():
    if (ROOT/'registration.json').exists():raise ValueError('Never overwrite a freeze')
    tests=list(ET.parse(ROOT/'tests/tests.xml').getroot().iter('testcase'))
    if not tests or any(t.find(x) is not None for t in tests for x in ('failure','error','skipped')):
        raise ValueError('Tests must pass before freeze')
    reg=load(ROOT/'registration-staged.json')
    for base in (ROOT/'code',ROOT/'tests',ROOT/'smoke'):
        for p in base.rglob('*'):
            if p.is_file() and not any(s in p.parts for s in ('__pycache__','.pytest_cache')):reg['files'][str(p)]=sha(p)
    for name in ('sol_provider.py','sol_install.py','worker.py','experiment.py','PROTOCOL.md',
                 'episode-budget.json','phase-budget.json','online-input-authorization.json'):
        reg['files'][str(ROOT/name)]=sha(ROOT/name)
    for path,digest in reg['files'].items():
        if not Path(path).is_file() or sha(path)!=digest:raise ValueError('Stale frozen input: '+path)
    reg.update(status='FROZEN_READY',tests_passed=len(tests),frozen=time.time(),new_eda_before_launch=0)
    record(ROOT/'registration.json',reg)
    print(json.dumps({'status':'FROZEN_READY','tasks':50,'tests':len(tests),'sha256':sha(ROOT/'registration.json')}))


def validate():
    reg=load(ROOT/'registration.json')
    if reg['status']!='FROZEN_READY' or reg['online_policy']!=I.policy():raise ValueError('Unregistered protocol')
    if (ROOT/'GLOBAL-STOP.json').exists():raise ValueError('Global provider/integrity stop')
    for path,digest in reg['files'].items():
        if sha(path)!=digest:raise ValueError('Frozen input changed: '+path)
    return reg


def episode_path(job):
    return ROOT/'episodes'/(job['task'].replace('/','__')+'__R2G_SOL_HIGH__r1')/'episode.json'


def collect():
    # Reuse the original main-table PPA binding and ledger cost definitions.
    import t2_freepdk45_main as M
    import t2_nb_followup as F
    rows=[]
    for job in load(ROOT/'registration.json')['plan']:
        path=episode_path(job);raw=load(path) if path.exists() else {'status':'NOT_STARTED'}
        events=[json.loads(s) for s in (path.parent/'reports/resource_ledger.jsonl').read_text().splitlines()
                if s] if (path.parent/'reports/resource_ledger.jsonl').exists() else []
        turns=raw.get('repair_turns') or []
        rows.append({**job,'design':job['task'].split('__')[0],
            'display_arm':'R2G (gpt-6.1-sol, high)','episode':str(path),
            'status':raw['status'],'raw':raw,
            'cost':F.ledger_cost(path.parent/'reports/resource_ledger.jsonl') if path.exists() else None,
            'harmful_attempts_observed':sum(bool((v.get('comparison') or {}).get('regressions')) for v in turns),
            'harmful_attempts_basis':'same explicit run-vector regressions as original Table 2; not all rollbacks',
            'rollback_attempts':sum(v.get('rolled_back') is True for v in turns),
            'reasoning_tokens_known':sum(e['reasoning_tokens'] for e in events if isinstance(e.get('reasoning_tokens'),int)),
            'reasoning_token_usage_incomplete':any(e.get('type')=='llm_request' and not isinstance(e.get('reasoning_tokens'),int) for e in events),
            'ppa':M.ppa(path.parent,raw) if path.exists() else {'status':'UNKNOWN'}})
    ended=[r for r in rows if r['status'] not in ('RUNNING','NOT_STARTED')]
    known=sum((r['raw'].get('token_usage') or {}).get('known_input_tokens',0)+
              (r['raw'].get('token_usage') or {}).get('known_output_tokens',0) for r in ended)
    unknown=sum((r['raw'].get('token_usage') or {}).get('unknown_usage_requests',0) for r in ended)
    summary={'registered':50,'ended':len(ended),'accepted':sum(r['status']=='ACCEPTED' for r in ended),
        'status_counts':dict(collections.Counter(r['status'] for r in rows)),
        'known_online_tokens':known,'actual_online_tokens':None if unknown else known,
        'unknown_requests':unknown,'requests':sum((r['raw'].get('accounting') or {}).get('llm_requests',0) for r in ended),
        'EDA_flows':sum((r['raw'].get('accounting') or {}).get('flows',0) for r in ended),
        'worker_wall_s':sum(r['raw'].get('total_worker_wall_s',0) for r in ended),
        'smoke_tokens_separate':load(ROOT/'smoke/result.json')['usage']['total_tokens'],
        'memory_construction_tokens':0,'cross_task_memory':False,'cost_usd':'UNKNOWN',
        'means_are_final':len(ended)==50}
    baseline_rows=[r for r in load(BASE/'table2-results.json')['episodes'] if r['arm']=='NB']
    baseline={r['task']:r for r in baseline_rows};paired=[];ppa_pairs=[]
    for row in ended:
        base=baseline[row['task']]
        if row['status'] in ('ACCEPTED','NOT_ACCEPTED') and base['status'] in ('ACCEPTED','NOT_ACCEPTED'):
            paired.append((row,base))
        if row['status']==base['status']=='ACCEPTED':ppa_pairs.append((row,base))
    metrics={}
    for key in ('stdcell_area_um2','core_area_um2','die_area_um2','total_power_w','setup_slack_ns'):
        changes=[]
        for a,b in ppa_pairs:
            x=(a['ppa'].get('values') or {}).get(key);y=(b['ppa'].get('values') or {}).get(key)
            delta=x-y if key=='setup_slack_ns' and x is not None and y is not None else M.relative(x,y)
            if delta is not None:changes.append(delta)
        metrics[key]={'n':len(changes),'mean_delta_ns' if key=='setup_slack_ns' else 'mean_relative_change':
                      sum(changes)/len(changes) if changes else None}
    summary.update(measured_pairs=len(paired),G=sum(a['status']=='ACCEPTED' and b['status']=='NOT_ACCEPTED' for a,b in paired),
        H=sum(a['status']=='NOT_ACCEPTED' and b['status']=='ACCEPTED' for a,b in paired),
        PPA_paired_accepted_vs_original_R2G=metrics,PPA_pair_count=len(ppa_pairs),
        harmful_attempts_observed=sum(r['harmful_attempts_observed'] for r in ended),
        rollback_attempts=sum(r['rollback_attempts'] for r in ended),
        cost_totals_so_far={k:sum((r['cost'] or {}).get(k,0) for r in ended)
                           for k in ('requests','known_tokens','unknown_requests','flows','tool_calls','checker_runs')})
    record(ROOT/'table2-extension-results.json',{'original_main_table':str(BASE/'table2-results.json'),
        'original_main_table_unchanged':True,'rows':rows,'summary':summary,
        'ppa_and_harm':'raw final/envelope, repair_turns, and resource ledgers remain authoritative'})
    # A separate combined view, never edit the old frozen table.
    original=load(BASE/'table2-results.json')
    record(ROOT/'table2-with-sol.json',{'original_main_table':str(BASE/'table2-results.json'),
        'original_main_table_sha256':sha(BASE/'table2-results.json'),
        'original_summaries':original.get('summary',original.get('summaries')),
        'additional_row':summary,'additional_display_arm':'R2G (gpt-6.1-sol, high)',
        'model_comparison_not_memory_ablation':True})


def audit(raw,events):
    acct=raw.get('accounting') or {};requests=[r for r in events if r.get('type')=='llm_request']
    if acct.get('llm_requests')!=len(requests) or len(requests)>3:raise ValueError('Request accounting/cap fault')
    if raw.get('status') in (None,'RUNNING'):raise ValueError('No terminal record')
    for req in requests:
        if req.get('model_mismatch') or req.get('model_reported') not in (None,P.MODEL):raise ValueError('Model identity fault')
        if req.get('model_reported')==P.MODEL and req.get('reasoning_effort_reported')!='high':raise ValueError('Reasoning effort fault')
    errors=set(acct.get('errors') or [])
    unknown=(raw.get('token_usage') or {}).get('unknown_usage_requests',0)
    if not acct.get('passed') and not (unknown>0 and errors=={'provider identity/input/usage violation'}):
        raise ValueError('Structural accounting failure: '+str(sorted(errors)))


def lane(index):
    reg=validate();slot=reg['scheduler']['cpu_slots'][index]
    with (ROOT/('lane-'+str(index)+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for job in reg['plan'][index::4]:
            validate();path=episode_path(job)
            if path.parent.exists():raise ValueError('Never retry a started episode')
            if shutil.disk_usage(ROOT).free<30*1024**3:raise RuntimeError('Disk safety floor')
            log=ROOT/'logs'/(job['task'].replace('/','__')+'.log');log.parent.mkdir(exist_ok=True)
            with log.open('x') as out:
                child=subprocess.Popen(['taskset','-c',','.join(map(str,slot)),PYTHON,str(ROOT/'worker.py'),
                    '--task',job['task']],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,
                    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':''})
                record(ROOT/('lane-'+str(index)+'-state.json'),{'status':'RUNNING','job':job,
                    'worker_pid':child.pid,'cpu_affinity':slot,'started':time.time()})
                child.wait()
            if not path.is_file():raise ValueError('Missing terminal/accounting receipt')
            raw=load(path);events=[json.loads(s) for s in (path.parent/'reports/resource_ledger.jsonl').read_text().splitlines() if s]
            audit(raw,events);collect()
        record(ROOT/('lane-'+str(index)+'-state.json'),{'status':'COMPLETED','ended':time.time()})


def run():
    validate()
    with (ROOT/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (ROOT/'launch.json').exists():raise ValueError('One launch only')
        if list((ROOT/'episodes').glob('*')):raise ValueError('Started episodes already exist')
        children=[]
        for index in range(4):
            out=(ROOT/('lane-'+str(index)+'.log')).open('x')
            child=subprocess.Popen([sys.executable,str(ROOT/'experiment.py'),'lane','--index',str(index)],
                cwd=ROOT,stdout=out,stderr=subprocess.STDOUT)
            children.append((index,child,out))
        record(ROOT/'launch.json',{'controller_pid':os.getpid(),'lanes':{str(i):p.pid for i,p,o in children},
            'started':time.time(),'registration_sha256':sha(ROOT/'registration.json')})
        for i,p,o in children:p.wait();o.close()
        collect()


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=['prepare','freeze','validate','collect','run','lane'])
    ap.add_argument('--index',type=int,choices=range(4));args=ap.parse_args()
    if args.command=='lane':
        try:lane(args.index)
        except Exception as exc:
            record(ROOT/('lane-'+str(args.index)+'-state.json'),{'status':'PAUSED','reason':str(exc)})
            if not (ROOT/'GLOBAL-STOP.json').exists():record(ROOT/'GLOBAL-STOP.json',{'reason':str(exc)})
            raise
    else:
        result=globals()[args.command]()
        if args.command=='validate':print(json.dumps({'status':'PASS','files':len(result['files'])}))
