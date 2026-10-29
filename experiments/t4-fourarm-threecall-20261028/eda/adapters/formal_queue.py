"""Four isolated lanes, paired order per host; local interruptions are deferred."""
import argparse
import collections
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
OLD=Path('/data1/zhangdy/tehm-experiments/t4-cross-host-online-20261026')
PYTHONS={'rtlfixer':OLD/'envs/rtlfixer/bin/python','rtlscout':OLD/'envs/modern/bin/python'}
HOSTS=('rtlfixer','rtlscout');ARMS=('native','tehm')


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp');tmp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');tmp.replace(path)


def roster():return json.loads((ROOT/'inputs/rtlrepair-roster.json').read_text())


def validate():
    reg=json.loads((ROOT/'inputs/registration.json').read_text())
    if not reg['launch_authorized']:raise ValueError('launch not authorized')
    for rel,h in reg['files'].items():
        if sha(ROOT/rel)!=h:raise ValueError('frozen file changed: '+rel)
    for filename,h in reg.get('external_files',{}).items():
        if sha(filename)!=h:raise ValueError('external input changed: '+filename)
    control=ROOT/'inputs/sixarm-control.json'
    if control.exists():
        umbrella=Path(json.loads(control.read_text())['root'])
        if (umbrella/'reports/GLOBAL-STOP.json').exists():raise ValueError('six-arm global stop')
    if (ROOT/'reports/GLOBAL-STOP.json').exists():raise ValueError('global identity/permission fault')
    return reg


def freeze():
    target=ROOT/'inputs/registration.json'
    if target.exists():raise ValueError('never replace an existing registration')
    for receipt in ('modern-tests-final.xml','fixer-tests-final.xml'):
        cases=list(ET.parse(ROOT/'reports'/receipt).getroot().iter('testcase'))
        if not cases or any(c.find('failure') is not None or c.find('error') is not None for c in cases):
            raise ValueError('test receipt missing or failed: '+receipt)
    smoke=json.loads((ROOT/'reports/native-offline-smoke.json').read_text())
    if smoke['status']!='PASS':raise ValueError('native full mechanism smoke not passed')
    files=[p for directory in ('adapters','code','inputs','tests') for p in (ROOT/directory).rglob('*')
           if p.is_file() and '__pycache__' not in p.parts]
    files += [ROOT/n for n in ('bootstrap.py','prepare_traces.py','launch_when_ready.py')]
    files += [ROOT/'reports'/n for n in ('modern-tests-final.xml','fixer-tests-final.xml','native-offline-smoke.json')]
    for task in json.loads((ROOT/'inputs/rtlrepair-private.json').read_text()):
        q=ROOT/'qualification'/task['task']/'qualification.json'
        if not q.exists():raise ValueError('qualification pass incomplete')
        files.append(q)
        for test in task['tests']:
            names=[*test.get('sources',[]),*test.get('init_files',[])]+[
                test[k] for k in ('oracle','table') if test.get(k)]
            files += [Path(name) for name in names]
        value=json.loads(q.read_text())
        files += [Path(value[k]) for k in ('reference_grade','baseline_grade') if value.get(k)]
    for directory in (ROOT/'sources').iterdir():
        names=subprocess.check_output(['git','-C',str(directory),'ls-files','-z']).split(b'\0')
        files += [directory/n.decode() for n in names if n and (directory/n.decode()).is_file()]
    value={'launch_authorized':True,'created':time.time(),'hosts':list(HOSTS),'arms':list(ARMS),
        'task_order':[t['task'] for t in roster()],'registered_episodes':308,
        'authorization':'User: RTLFixer 和 RTLScout 都接入完整 RTLRepair; run remaining formal Table 4 groups',
        'model':'deepseek-flash','repeat':1,'initial_memory':'empty private stores, online write-back',
        'shared_core_algorithm_changes':False,'native_tools_and_guidance_preserved':True,
        'cumulative_tokens_cap':None,'cumulative_money_cap':None,'implicit_provider_retries':0,
        'interruption_policy':'preserve actual UNKNOWN, stop affected episode only, defer until a separate retry round',
        'scope':'complete ASPLOS24 RTLRepair registered corpus; upstream output oracles via Icarus and qualified zero-initial CSV replay via Verilator, not official VCS/gate-level reproduction',
        'verification_gate':'reference PASS; buggy measured; no compilation-only success; unsupported/UNKNOWN tasks retained',
        'native_task_limits':{'rtlfixer':'native simulation budget 10, CodeAgent/FixAgent max_iterations 20, RAG retained',
                              'rtlscout':'native ReAct 20 decision steps plus separately metered native summary'},
        'files':{str(p.relative_to(ROOT)):sha(p) for p in sorted(set(files))}}
    record(target,value);validate()
    print(json.dumps({'status':'FROZEN','registered_episodes':308,'sha256':sha(target)}))


def summary():
    rows=[]
    for task in roster():
        for host in HOSTS:
            for arm in ARMS:
                directory=ROOT/'hosts'/host/arm/'episodes'/task['task']
                raw=directory/'episode.json';fallback=directory/'terminal-accounting.json'
                deferred=ROOT/'reports/deferred'/f"{host}--{arm}--{task['task']}.json"
                path=raw if raw.exists() else fallback if fallback.exists() else deferred
                if path.exists():row=json.loads(path.read_text())
                else:row={'host':host,'arm':arm,'task':task['task'],'status':'RUNNING' if (directory/'started.json').exists() else 'NOT_STARTED'}
                rows.append(row)
    retries=[{k:r[k] for k in ('host','arm','task','status')} for r in rows
             if r['status'] not in {'ACCEPTED','NOT_ACCEPTED','RUNNING','NOT_STARTED'}]
    record(ROOT/'reports/table4-eda-results.json',{'registered':308,'episodes':rows,
        'status_counts':dict(collections.Counter(r['status'] for r in rows)),
        'completed_cells':sum(r['status'] not in {'RUNNING','NOT_STARTED'} for r in rows),
        'scope':'RTLRepair local Icarus output-oracle and qualified zero-initial Verilator trace adaptation; technical/qualification stops not repair failures'})
    record(ROOT/'reports/retry-manifest.json',{'automatic_retries':False,'episodes':retries})
    return rows


def defer(host,arm,task,status,reason):
    path=ROOT/'reports/deferred'/f'{host}--{arm}--{task}.json'
    if path.exists():raise ValueError('deferred task already exists')
    record(path,{'host':host,'arm':arm,'task':task,'status':status,'reason':reason,
        'strict_accepted':None,'measured_repair_outcome':False,'retry_in_original_queue':False,'created':time.time()})


def lane(host):
    with (ROOT/'reports'/f'{host}-dispatcher.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for task in roster():
            validate()
            instance=task['task'];receipt=ROOT/'qualification'/instance/'qualification.json'
            record(ROOT/'reports'/f'{host}-state.json',{'status':'WAITING_QUALIFICATION','task':instance,'updated':time.time()})
            while not receipt.exists():
                if (ROOT/'reports/GLOBAL-STOP.json').exists():return
                time.sleep(5)
            q=json.loads(receipt.read_text())
            if q['status']!='PASS':
                for arm in ARMS:defer(host,arm,instance,'QUALIFICATION_BLOCKED',q.get('reason'))
                continue
            workers=[]
            for arm in ARMS:
                directory=ROOT/'hosts'/host/arm/'episodes'/instance
                if directory.exists():raise ValueError('episode already exists; no retry')
                logs=ROOT/'reports/worker-logs';logs.mkdir(exist_ok=True)
                out=(logs/f'{host}--{arm}--{instance}.log').open('x')
                proc=subprocess.Popen([str(PYTHONS[host]),str(ROOT/'adapters/formal_runtime.py'),
                    '--host',host,'--arm',arm,'--task',instance],stdout=out,stderr=subprocess.STDOUT,cwd=ROOT)
                workers.append((arm,proc,out))
            record(ROOT/'reports'/f'{host}-state.json',{'status':'RUNNING','task':instance,
                'workers':{a:p.pid for a,p,_ in workers},'updated':time.time()})
            for arm,proc,out in workers:
                proc.wait();out.close()
                directory=ROOT/'hosts'/host/arm/'episodes'/instance
                if not (directory/'terminal-accounting.json').exists():
                    directory.mkdir(parents=True,exist_ok=True)
                    record(directory/'terminal-accounting.json',{'host':host,'arm':arm,'task':instance,
                        'status':'WORKER_CRASH','strict_accepted':None,'returncode':proc.returncode})
            validate();summary() # All global faults remain fail-closed.
        record(ROOT/'reports'/f'{host}-state.json',{'status':'QUEUE_COMPLETE','updated':time.time(),'automatic_retries':0})
        summary()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['freeze','summary','run']);p.add_argument('--host',choices=HOSTS)
    a=p.parse_args()
    if a.command=='run':
        if not a.host:p.error('--host required')
        try:lane(a.host)
        except Exception as exc:
            record(ROOT/'reports'/f'{a.host}-state.json',{'status':'GLOBAL_BLOCKED',
                'reason':str(exc),'exception':type(exc).__name__,'updated':time.time()})
            raise
    else:{'freeze':freeze,'summary':summary}[a.command]()
