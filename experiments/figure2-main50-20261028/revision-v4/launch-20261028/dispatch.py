"""Three-lane operator for the immutable v4 runtime; no implicit reruns."""
import argparse
import collections
import fcntl
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parent
REVISION=ROOT.parent
sys.path.insert(0,str(REVISION))
import experiment as E


def episode_path(job):
    return Path(job['root'])/'episodes'/(job['task'].replace('/','__')+'__'+job['variant']+'__r1')/'episode.json'


def validate():
    reg=E.load(ROOT/'registration.json')
    for p,h in reg['files'].items():
        if E.sha(p)!=h:raise ValueError('Operator registration changed: '+p)
    if E.sha(REVISION/'registration.json')!=reg['revision_sha256']:
        raise ValueError('Frozen runtime revision changed')
    if not reg.get('user_authorized_parallel_launch'):raise ValueError('No launch authorization')
    if (ROOT/'PAUSE.json').exists() or (REVISION/'GLOBAL-STOP.json').exists():
        raise ValueError('Operator or runtime is paused')
    return reg


def validate_namespaces(plan):
    old={(j['task'],j['variant']) for j in plan['carried_cells']}
    new=[(j['task'],j['variant']) for j in plan['jobs']]
    if len(new)!=len(set(new)) or set(new)&old:raise ValueError('Execution namespaces overlap')
    for job in plan['jobs']:
        if (Path(job['source_root'])/'episodes').exists():raise ValueError('Old queue started a reserved cell')
    return True


def prior_dispatch_paused():
    launch=E.load(E.PRIOR/'launch.json')
    for variant,pid in launch['lanes'].items():
        directory=Path('/proc')/str(pid)
        if not directory.exists():continue
        args=(directory/'cmdline').read_bytes().split(b'\0')
        if str(E.PRIOR/'resume.py').encode() not in args or variant.encode() not in args:
            raise ValueError('Old dispatcher PID identity changed')
        state=(directory/'stat').read_text().rsplit(')',1)[1].split()[0]
        if state not in ('T','t','Z'):raise ValueError('Old dispatcher is not paused')


def audit_episode(raw,events,variant):
    acct=raw.get('accounting') or {}
    calls=acct.get('llm_requests')
    requests=[e for e in events if e.get('type')=='llm_request']
    if (isinstance(calls,bool) or not isinstance(calls,int) or not 0<=calls<=3 or
        calls!=len(requests) or (variant=='RECIPE' and calls)):
        raise ValueError('Online request accounting/cap violation')
    local=sum(e.get('type')=='figure2_local_attempt' for e in events)
    if local>3 or (raw.get('agent_run') or {}).get('local_memory_attempts',local)!=local:
        raise ValueError('Local action accounting/cap violation')
    for req in requests:
        if req.get('model_mismatch') or req.get('model_reported') not in (None,'deepseek-flash','deepseek-v4.1-flash'):
            raise ValueError('Provider identity fault')
    errors=set(acct.get('errors') or [])
    unknown=(raw.get('token_usage') or {}).get('unknown_usage_requests',0)
    # Standing operator rule: retain an UNKNOWN transport interruption and skip
    # only that cell. It never becomes zero usage or a measured repair failure.
    if not acct.get('passed') and not (unknown>0 and errors=={'provider identity/input/usage violation'}):
        raise ValueError('Structural accounting fault: '+str(sorted(errors)))
    if raw.get('status') in (None,'RUNNING','NOT_STARTED'):
        raise ValueError('Worker has no terminal record')
    return {'requests':calls,'local_attempts':local,'unknown_usage_requests':unknown,
            'status':raw['status'],'unknown_is_not_zero':True}


def collect():
    rows=[]
    for job in E.load(REVISION/'plan.json')['jobs']:
        path=episode_path(job);raw=E.load(path) if path.exists() else {'status':'NOT_STARTED'}
        rows.append({**job,'episode':str(path),'runtime_revision':'v4','status':raw['status'],'raw':raw})
    E.record(ROOT/'results.json',{'revision':'v4','new_registered_cells':len(rows),'rows':rows,
        'status_counts':dict(collections.Counter(r['status'] for r in rows)),
        'prior_24_cells':str(REVISION/'plan.json'),'old_results_not_merged_into_v4':True,
        'cost_source':'each original episode.json and reports/resource_ledger.jsonl'})


def lane(variant):
    validate();plan=E.load(REVISION/'plan.json');slot=plan['slots'][E.VARIANTS.index(variant)]
    with (ROOT/(variant+'.lock')).open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for job in [j for j in plan['jobs'] if j['variant']==variant]:
            validate();prior_dispatch_paused();E.validate(job['root'])
            if (Path(job['root'])/'episodes').exists() or (Path(job['source_root'])/'episodes').exists():
                raise ValueError('Never rerun or overlap a started cell')
            if shutil.disk_usage(REVISION).free<30*1024**3:raise RuntimeError('Disk safety floor')
            log=ROOT/(variant+'--'+job['design']+'.log')
            with log.open('x') as output:
                worker=subprocess.Popen(['taskset','-c',','.join(map(str,slot)),E.PYTHON,
                    str(REVISION/'worker.py'),'--root',job['root'],'--task',job['task'],'--arm',job['arm']],
                    cwd=REVISION,stdout=output,stderr=subprocess.STDOUT,
                    env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':''})
                E.record(ROOT/(variant+'-state.json'),{'status':'RUNNING','job':job,
                    'worker_pid':worker.pid,'cpu_affinity':slot,'started':time.time()})
                worker.wait()
            path=episode_path(job)
            if not path.is_file():raise ValueError('Worker terminal/accounting file missing')
            raw=E.load(path);journal=path.parent/'reports/resource_ledger.jsonl'
            events=[json.loads(l) for l in journal.read_text().splitlines() if l]
            audit=audit_episode(raw,events,variant)
            E.record(ROOT/(variant+'--'+job['design']+'--audit.json'),audit)
            collect()
        E.record(ROOT/(variant+'-state.json'),{'status':'COMPLETED','ended':time.time()})


def freeze():
    if (ROOT/'registration.json').exists():raise ValueError('Never overwrite an operator freeze')
    cases=list(ET.parse(ROOT/'tests.xml').getroot().iter('testcase'))
    if not cases or any(c.find(k) is not None for c in cases for k in ('failure','error','skipped')):
        raise ValueError('Operator tests must pass')
    E.validate();plan=E.load(REVISION/'plan.json');validate_namespaces(plan);prior_dispatch_paused()
    files={str(ROOT/n):E.sha(ROOT/n) for n in ('dispatch.py','test_dispatch.py','tests.xml')}
    E.record(ROOT/'registration.json',{'schema':'figure2-v4-parallel-operator-v1','files':files,
        'revision_sha256':E.sha(REVISION/'registration.json'),'user_authorized_parallel_launch':True,
        'authorization':'User requested pause old version, run corrected new version concurrently',
        'lanes':list(E.VARIANTS),'new_cells':len(plan['jobs']),'tests_passed':len(cases),
        'old_dispatch_stays_paused':True,'old_started_workers_not_interrupted':True,
        'old_and_new_namespaces_disjoint':True,'reruns':False,'created':time.time()})
    print(json.dumps({'operator_frozen':True,'cells':len(plan['jobs']),'tests':len(cases)}))


def run():
    validate();E.validate();plan=E.load(REVISION/'plan.json');validate_namespaces(plan);prior_dispatch_paused()
    with (ROOT/'controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (ROOT/'launch.json').exists():raise ValueError('Launch already exists; no implicit restart')
        children=[]
        for variant in E.VARIANTS:
            output=(ROOT/(variant+'-lane.log')).open('x')
            child=subprocess.Popen([sys.executable,str(ROOT/'dispatch.py'),'lane','--variant',variant],
                cwd=REVISION,stdout=output,stderr=subprocess.STDOUT)
            children.append((variant,child,output))
        E.record(ROOT/'launch.json',{'controller_pid':os.getpid(),'started':time.time(),
            'lanes':{v:c.pid for v,c,o in children},'revision_sha256':E.sha(REVISION/'registration.json'),
            'operator_registration_sha256':E.sha(ROOT/'registration.json')})
        for variant,child,output in children:child.wait();output.close()
        collect()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','run','lane','collect'])
    parser.add_argument('--variant',choices=E.VARIANTS);args=parser.parse_args()
    if args.command=='lane':
        try:lane(args.variant)
        except Exception as exc:
            E.record(ROOT/(args.variant+'-state.json'),{'status':'PAUSED','reason':str(exc),'ended':time.time()})
            if not (ROOT/'PAUSE.json').exists():
                E.record(ROOT/'PAUSE.json',{'reason':str(exc),'variant':args.variant})
            raise
    else:globals()[args.command]()
