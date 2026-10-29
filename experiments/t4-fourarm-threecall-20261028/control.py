"""Four independent chronological lanes; RTLFixer arms explicitly excluded."""
import argparse
import collections
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
BASE=ROOT.parent
PYTHONS={
    'rtlscout':BASE/'t4-cross-host-online-20261026/envs/modern/bin/python',
    'swe-agent':BASE/'t4-swe-agent-adaptation-20261026/envs/runtime/bin/python'}
LANES=[(h,a) for h in PYTHONS for a in ('native','tehm')]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def record(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    temp.write_text(json.dumps(value,indent=2,sort_keys=True)+'\n');temp.replace(path)


def package(host):return ROOT/('swe' if host=='swe-agent' else 'eda')


def tasks(host):
    if host=='swe-agent':
        return [t['instance_id'] for t in json.loads((package(host)/'inputs/public-tasks.json').read_text())['public_tasks']]
    return [t['task'] for t in json.loads((package(host)/'inputs/rtlrepair-roster.json').read_text())]


def identity(host,arm,task):return host+'--'+arm+'--'+task


def episode(host,arm,task):return package(host)/'hosts'/host/arm/'episodes'/task


def frozen_files(root):
    paths=[]
    for name in ('adapters','tests','inputs','code/memory'):
        for path in (root/name).rglob('*'):
            if path.is_file() and not any(p in path.parts for p in ('__pycache__','.git','.pytest_cache')) and path.name!='registration.json':
                paths.append(path)
    if root.name=='eda':
        for directory in (root/'sources').iterdir():
            paths.extend(p for p in directory.rglob('*') if p.is_file()
                and not any(c in p.parts for c in ('.git','__pycache__','.pytest_cache')))
        paths.extend(p for p in (root/'bin').iterdir() if p.is_file())
    else:
        paths.extend((root/'bootstrap').glob('*.whl'))
        paths.extend(p for p in (root/'sources/SWE-bench-v4.1.0').rglob('*') if p.is_file()
                     and not any(c in p.parts for c in ('.git','__pycache__')))
    paths.extend((root/'qualification').glob('*/qualification.json'))
    return sorted(set(paths))


def paths_in(value):
    if isinstance(value,dict):
        for child in value.values():yield from paths_in(child)
    elif isinstance(value,list):
        for child in value:yield from paths_in(child)
    elif isinstance(value,str) and value.startswith('/') and '\n' not in value:
        try:
            p=Path(value)
            if p.is_file():yield p
        except OSError:pass


def external_inputs(root):
    result={}
    private=root/('inputs/evaluator-private/tasks.json' if root.name=='swe' else 'inputs/rtlrepair-private.json')
    for p in paths_in(json.loads(private.read_text())):result[str(p)]=sha(p)
    for q in (root/'qualification').glob('*/qualification.json'):
        item=json.loads(q.read_text())
        for p in paths_in(item):result[str(p)]=sha(p)
        if item['status']!='PASS':continue
        for label,wanted in [('baseline','FAIL'),('reference','PASS')]:
            if root.name=='swe':
                suffix='-r3' if item.get('previous_receipt')=='qualification-before-runtime-pythonpath.json' else ''
                original=BASE/'t4-swe20-local-20261026/qualification'/q.parent.name
                p=Path(item.get(label+'_grade',str(original/(label+'-eval'+suffix)/'grade.json')))
            else:p=Path(item[label+'_grade'])
            grade=json.loads(p.read_text())
            valid=grade['status']==wanted
            if root.name=='eda' and label=='baseline' and grade['status']=='PASS':
                valid=item.get('initially_passing') is True
            if not valid:raise ValueError('qualification proof invalid or initially passing flag absent: '+str(p))
            result[str(p)]=sha(p)
            if root.name=='swe':
                output=p.parent/'test-output.txt'
                if sha(output)!=grade['test_output_sha256']:raise ValueError('qualification output changed')
                result[str(output)]=sha(output)
                # Store explicit links so this package never invents local proof paths.
                if item.get(label+'_grade')!=str(p):
                    raise ValueError('staged SWE qualification needs explicit grade links: '+str(q))
    if root.name=='swe':
        original=json.loads((BASE/'t4-swe20-local-20261026/inputs/registration.json').read_text())
        for filename,digest in original['external_files'].items():
            if sha(filename)!=digest:raise ValueError('native upstream changed: '+filename)
            result[filename]=digest
    return result


def audit_episode(host,arm,task):
    directory=episode(host,arm,task)
    journal=directory/'provider/requests.jsonl'
    rows=[json.loads(s) for s in journal.read_text().splitlines()] if journal.exists() else []
    starts=[r for r in rows if r['event']=='request_started']
    ends=[r for r in rows if r['event']=='request_ended']
    if len(starts)>3:raise ValueError('online request limit violated: '+str(directory))
    if len({r['request_local_id'] for r in starts})!=len(starts):raise ValueError('duplicate request id')
    if {r['request_local_id'] for r in ends}-{r['request_local_id'] for r in starts}:
        raise ValueError('unowned usage settlement')
    path=directory/('result.json' if host=='swe-agent' else 'terminal-accounting.json')
    if not path.exists():raise ValueError('worker missing terminal accounting: '+str(directory))
    terminal=json.loads(path.read_text())
    usage=terminal.get('usage',terminal.get('cost',{}))
    if usage.get('requests',usage.get('online_requests'))!=len(starts):raise ValueError('terminal request count mismatch')
    known=sum(r['usage']['total_tokens'] for r in ends if isinstance(r.get('usage'),dict))
    if usage.get('known_tokens')!=known:raise ValueError('terminal token count mismatch')
    incomplete=len(starts)!=len(ends) or any(r.get('usage')=='UNKNOWN' for r in ends)
    if incomplete and (usage.get('actual_total_tokens') is not None or terminal['status'] in ('ACCEPTED','NOT_ACCEPTED')):
        raise ValueError('unverified usage scored as a measured outcome')
    return {'status':'PASS','host':host,'arm':arm,'task':task,'requests':len(starts),'unknown_or_open':incomplete,'known_tokens':known}


def freeze():
    if (ROOT/'registration.json').exists():raise ValueError('never overwrite registration')
    tests={}
    for label in ('scout','swe','controller'):
        path=ROOT/'reports'/(label+'-tests.xml')
        cases=list(ET.parse(path).getroot().iter('testcase'))
        if not cases or any(c.find('failure') is not None or c.find('error') is not None for c in cases):
            raise ValueError('test receipt absent/failed: '+label)
        tests[label]={'passed':sum(c.find('skipped') is None for c in cases),
                      'skipped':sum(c.find('skipped') is not None for c in cases),'sha256':sha(path)}
    if any((ROOT/p/'hosts').exists() for p in ('eda','swe')):raise ValueError('not an empty initial memory')
    if (ROOT/'swe/inputs/native.yaml').read_bytes()!=(ROOT/'swe/inputs/tehm.yaml').read_bytes():
        raise ValueError('SWE native configuration differs between arms')
    registrations={}
    for name in ('eda','swe'):
        root=ROOT/name
        files={str(p.relative_to(root)):sha(p) for p in frozen_files(root)}
        reg={'schema':'table4-fourarm-threecall-v1','launch_authorized':True,
             'runtime_label':'table4-fourarm-threecall-20261028',
             'files':files,'external_files':external_inputs(root),
             'online_request_limit_per_design_per_arm':3,
             'memory_policy':'fresh private empty stores; chronological online write-back; no old trajectories',
             'model':'deepseek-flash','native_commit':'0f3acafacabc0def8cc76b4e48acb4b6cf302cb9' if name=='swe' else None}
        reg['hosts']=['swe-agent'] if name=='swe' else ['rtlscout']
        reg['arms']=['native','tehm']
        reg['task_order']=tasks('swe-agent' if name=='swe' else 'rtlscout')
        record(root/'inputs/registration.json',reg)
        registrations[name]=sha(root/'inputs/registration.json')
    topfiles=[ROOT/'control.py',ROOT/'prepare.py',ROOT/'finalize_inputs.py',ROOT/'PROTOCOL.md']+list((ROOT/'tests').glob('*.py'))
    value={'schema':'table4-fourarm-threecall-v1','status':'FROZEN_READY','launch_authorized':True,
           'user_request':'Table4原生协议三次LLM并发重跑；最新指令排除RTLFixer、RTLFixer＋TEHM',
           'excluded_hosts':['rtlfixer'],'lanes':LANES,'registered_cells':194,'repeat':1,'model':'deepseek-flash',
           'online_requests_per_task_per_arm':3,'includes':['repair','reflection','retrieval assistance','summary','self-verification','retry'],
           'cumulative_token_cap':None,'cumulative_money_cap':None,'implicit_retries':0,
           'memory_initialization':'fresh private stores for each host/arm, chronological task writes; old results untouched',
           'qualified_cells':sum(json.loads((package(h)/'qualification'/t/'qualification.json').read_text())['status']=='PASS'
                                 for h,a in LANES for t in tasks(h)),
           'interruption_policy':'local transport UNKNOWN preserved and skipped without retry; global payment/permission/identity/integrity faults block new requests',
           'disk_floor_gib':50,'package_registrations':registrations,'tests':tests,
           'files':{str(p.relative_to(ROOT)):sha(p) for p in topfiles},'created':time.time()}
    record(ROOT/'registration.json',value)
    validate()
    print(json.dumps({'status':'FROZEN_READY','registration_sha256':sha(ROOT/'registration.json'),
                      'registered':194,'qualified':value['qualified_cells'],'tests':tests}))


def validate():
    reg=json.loads((ROOT/'registration.json').read_text())
    if not reg['launch_authorized']:raise ValueError('not authorized')
    if (ROOT/'reports/GLOBAL-STOP.json').exists():raise ValueError('global stop')
    for rel,digest in reg['files'].items():
        if sha(ROOT/rel)!=digest:raise ValueError('controller freeze mismatch: '+rel)
    for name,digest in reg['package_registrations'].items():
        root=ROOT/name
        if sha(root/'inputs/registration.json')!=digest:raise ValueError('registration changed: '+name)
        sub=json.loads((root/'inputs/registration.json').read_text())
        for rel,h in sub['files'].items():
            if sha(root/rel)!=h:raise ValueError('frozen runtime changed: '+str(root/rel))
        for filename,h in sub['external_files'].items():
            if sha(filename)!=h:raise ValueError('external input changed: '+filename)
    return reg


def summary():
    rows=[]
    for host,arm in LANES:
        for task in tasks(host):
            directory=episode(host,arm,task)
            candidates=[directory/('result.json' if host=='swe-agent' else 'terminal-accounting.json'),
                        ROOT/'reports/deferred'/(identity(host,arm,task)+'.json')]
            path=next((p for p in candidates if p.exists()),None)
            row=json.loads(path.read_text()) if path else {'status':'RUNNING' if directory.exists() else 'NOT_STARTED'}
            rows.append({**row,'host':host,'arm':arm,'task':task})
    record(ROOT/'reports/results.json',{'registered':194,'episodes':rows,
        'status_counts':dict(collections.Counter(r['status'] for r in rows)),
        'ended':sum(r['status'] not in ('RUNNING','NOT_STARTED') for r in rows),
        'not_official_docker_leaderboard':True,'all_registered_cells_retained':True})
    fields=['host','arm','task','status','strict_accepted','initially_passing','online_requests','known_tokens','unknown_requests',
            'actual_total_tokens','worker_wall_s','tool_calls','harmful_attempts','memory_writes']
    temp=ROOT/'reports'/('results.'+str(os.getpid())+'.tmp')
    with temp.open('w',newline='') as out:
        writer=csv.DictWriter(out,fields);writer.writeheader()
        for row in rows:
            usage=row.get('cost',row.get('usage',{}))
            flat={**row,**usage,'online_requests':usage.get('online_requests',usage.get('requests')),
                  'worker_wall_s':usage.get('worker_wall_s',row.get('wall_s')),
                  'harmful_attempts':row.get('harmful_attempts_observed',row.get('harmful_attempts'))}
            writer.writerow({k:flat.get(k) for k in fields})
    temp.replace(ROOT/'reports/results.csv')
    return rows


def lane(host,arm):
    state=ROOT/'reports'/f'{host}--{arm}-state.json'
    with (ROOT/'reports'/f'{host}--{arm}.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            validate()
            for task in tasks(host):
                validate()
                q=json.loads((package(host)/'qualification'/task/'qualification.json').read_text())
                if q['status']!='PASS':
                    record(ROOT/'reports/deferred'/(identity(host,arm,task)+'.json'),
                        {'host':host,'arm':arm,'task':task,'status':'QUALIFICATION_BLOCKED','reason':q.get('reason'),
                         'strict_accepted':None,'measured_repair_outcome':False,'model_requests':0})
                    continue
                if shutil.disk_usage(ROOT).free<50*1024**3:raise RuntimeError('disk safety floor: fewer than 50 GiB free')
                if episode(host,arm,task).exists():raise ValueError('already started; never repeat an episode')
                command=[str(PYTHONS[host]),str(package(host)/'adapters'/('run_swe.py' if host=='swe-agent' else 'formal_runtime.py')),
                         '--arm',arm,'--task',task]
                if host!='swe-agent':command+=['--host',host]
                logs=ROOT/'reports/worker-logs';logs.mkdir(exist_ok=True)
                with (logs/(identity(host,arm,task)+'.log')).open('x') as out:
                    worker=subprocess.Popen(command,cwd=package(host),stdout=out,stderr=subprocess.STDOUT)
                    record(state,{'status':'RUNNING','task':task,'worker_pid':worker.pid,'lane_pid':os.getpid(),'updated':time.time()})
                    worker.wait()
                receipt=audit_episode(host,arm,task)
                record(ROOT/'reports/integrity'/(identity(host,arm,task)+'.json'),receipt)
                summary()
            record(state,{'status':'QUEUE_COMPLETE','updated':time.time(),'automatic_retries':0})
            summary()
        except Exception as exc:
            record(state,{'status':'PAUSED','reason':str(exc),'exception':type(exc).__name__,'updated':time.time()})
            if not isinstance(exc,RuntimeError):
                record(ROOT/'reports/GLOBAL-STOP.json',{'reason':str(exc),'lane':[host,arm],'created':time.time()})
            raise


def launch():
    validate()
    with (ROOT/'reports/controller.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (ROOT/'reports/launch.json').exists():raise ValueError('one launch only; no implicit resume')
        children=[]
        for host,arm in LANES:
            log=(ROOT/'reports'/f'{host}--{arm}-lane.log').open('x')
            p=subprocess.Popen([sys.executable,str(ROOT/'control.py'),'lane','--host',host,'--arm',arm],
                               cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            children.append((host,arm,p,log))
        record(ROOT/'reports/launch.json',{'pid':os.getpid(),'started':time.time(),
            'registration_sha256':sha(ROOT/'registration.json'),
            'lanes':{h+'--'+a:p.pid for h,a,p,_ in children}})
        for h,a,p,log in children:p.wait();log.close()
        summary()
        record(ROOT/'reports/controller-final.json',{'ended':time.time(),'returncodes':{h+'--'+a:p.returncode for h,a,p,_ in children}})


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['freeze','validate','summary','launch','lane'])
    parser.add_argument('--host',choices=list(PYTHONS));parser.add_argument('--arm',choices=['native','tehm'])
    args=parser.parse_args()
    if args.command=='lane':
        if not args.host or not args.arm:parser.error('host and arm required')
        lane(args.host,args.arm)
    else:globals()[args.command]()
