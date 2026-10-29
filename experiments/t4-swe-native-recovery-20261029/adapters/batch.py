"""Two matched lanes, fixed task order, chronological memory, no episode reruns."""
import argparse
import csv
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import time
import xml.etree.ElementTree as ET

from select_tasks import ROOT
from provision import record
from admission import admission_blockers, interruption_policy

PREP=ROOT.parent/'t4-swe-agent-adaptation-20261026'
PYTHON=PREP/'envs/runtime/bin/python'


def roster():
    return json.loads((ROOT/'inputs/public-tasks.json').read_text())['public_tasks']


def summary():
    rows=[]
    for task in roster():
        for arm in ('native','tehm'):
            episode=ROOT/'hosts/swe-agent'/arm/'episodes'/task['instance_id']
            path=episode/'result.json'
            crash=episode/'controller-crash.json'
            deferred=ROOT/'reports/deferred-episodes'/(task['instance_id']+'--'+arm+'.json')
            row=json.loads(path.read_text()) if path.exists() else json.loads(crash.read_text()) if crash.exists() else json.loads(deferred.read_text()) if deferred.exists() else {
                'task':task['instance_id'],'arm':arm,
                'status':'RUNNING' if (episode/'started.json').exists() else 'NOT_STARTED'}
            rows.append(row)
    record(ROOT/'reports/results.json',{'registered_episodes':40,'episodes':rows,
        'completed':sum(r['status'] not in {'RUNNING','NOT_STARTED'} for r in rows),
        'executed_episodes':sum((ROOT/'hosts/swe-agent'/r['arm']/'episodes'/r['task']/'started.json').exists() for r in rows),
        'qualification_blocked_cells':sum(r['status']=='QUALIFICATION_BLOCKED' for r in rows),
        'measured_outcomes':sum(r['status'] in {'ACCEPTED','NOT_ACCEPTED'} for r in rows),
        'paper_scope':'SWE-bench Verified local reproduction subset, not official Docker leaderboard'})
    incomplete=[{'task':r['task'],'arm':r['arm'],'status':r['status'],
                 'automatic_retry':False,'handling':'separate retry round after this queue finishes'}
                for r in rows if r['status'] not in {'ACCEPTED','NOT_ACCEPTED','RUNNING','NOT_STARTED'}]
    record(ROOT/'reports/retry-manifest.json',{'automatic_retry':False,'episodes':incomplete,
           'note':'original records remain primary; retries require distinct attempt identities and costs'})
    with (ROOT/'reports/results.csv').open('w',newline='') as out:
        fields=['task','arm','status','strict_accepted','requests','known_tokens','unknown_requests',
                'actual_total_tokens','tool_calls','memory_consults','memory_selected','wall_s','harmful_attempts']
        writer=csv.DictWriter(out,fields);writer.writeheader()
        for r in rows:
            flat={**r,**r.get('usage',{})}
            writer.writerow({k:flat.get(k) for k in fields})
    return rows


def freeze():
    if (ROOT/'inputs/registration.json').exists():
        raise ValueError('registration exists; never overwrite')
    cases=list(ET.parse(ROOT/'reports/local-tests.xml').getroot().iter('testcase'))
    if len(cases)<8 or any(c.find('failure') is not None or c.find('error') is not None or c.find('skipped') is not None for c in cases):
        raise ValueError('local tests missing or failed')
    pin=json.loads((ROOT/'inputs/dataset-pin-verification.json').read_text())
    if pin['status']!='PASS':
        raise ValueError('pinned input data unverified')
    smoke=ROOT/'reports/native-real-tools-smoke-r3/receipt.json'
    if not smoke.exists() or json.loads(smoke.read_text()).get('status')!='PASS':
        raise ValueError('real native edit/bash/submit smoke has not passed')
    files=[p for tree in ('adapters','code/memory') for p in (ROOT/tree).rglob('*.py')
           if '__pycache__' not in p.parts]
    files += list((ROOT/'bootstrap').glob('*.whl'))
    files += [ROOT/'inputs'/n for n in ('public-tasks.json','native.yaml','tehm.yaml',
              'dataset-pin-verification.json','eligibility-receipt.json','evaluator-private/tasks.json')]
    files += [smoke,ROOT/'reports/local-tests.xml']
    files += [p for p in (ROOT/'sources/SWE-bench-v4.1.0').rglob('*')
              if p.is_file() and '.git' not in p.parts and '__pycache__' not in p.parts]
    native_root=PREP/'sources/SWE-agent'
    native_names=subprocess.check_output(['git','-C',str(native_root),'ls-files','-z']).split(b'\0')
    external={str(native_root/os_name.decode()):hashlib.sha256((native_root/os_name.decode()).read_bytes()).hexdigest()
              for os_name in native_names if os_name and (native_root/os_name.decode()).is_file()}
    if (ROOT/'inputs/native.yaml').read_bytes()!=(ROOT/'inputs/tehm.yaml').read_bytes():
        raise ValueError('native tool/template/model config parity broken')
    native_commit=subprocess.check_output(['git','-C',str(PREP/'sources/SWE-agent'),
                                          'rev-parse','HEAD'],text=True).strip()
    record(ROOT/'inputs/registration.json',{'launch_authorized':True,
        'authorization':'User: each host 20 tasks x native/TEHM x once; run SWE first',
        'registered_tasks':20,'episodes':40,'arms':['native','tehm'],
        'native_commit':native_commit,'model':'deepseek-flash',
        'external_files':external,
        'scope':'repository-stratified fixed Verified subset, local reproduction; not official Docker score',
        'memory':'empty private TEHM store; observations/terminal outcomes written online; each task retrieves only its task-start snapshot; later tasks see previous writes',
        'native_stopping':'unmodified native submit, errors, format-requery and tool timeout rules; no Table2 three-call limit',
        'cumulative_tokens_cap':None,'cumulative_money_cap':None,
        'implicit_request_retries':0,'unknown_policy':'preserve actual UNKNOWN; pause new admissions',
        'task_order':[r['instance_id'] for r in roster()],
        'qualification':'per-task baseline FAIL + reference PASS, no missing required tests, before paid calls',
        'files':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
        'created':time.time()})
    summary()


def continuation_plan(root,registration,tasks):
    """Never rerun a prior episode; the authorized preserved prefix is explicit."""
    authorization=json.loads((root/registration['continuation_authorization']).read_text())
    preserved=authorization.get('preserved_tasks',[authorization.get('preserved_task')])
    if not preserved or preserved!=[t['instance_id'] for t in tasks[:len(preserved)]]:
        raise ValueError('preserved task prefix mismatch')
    remaining=tasks[len(preserved):]
    expected=[(t['instance_id'],a) for t in remaining for a in ('native','tehm')]
    if authorization['remaining_episodes']!=[list(x) for x in expected] or len(tasks)!=20:
        raise ValueError(f'continuation roster is not exactly the authorized remaining {len(expected)}')
    for task in preserved:
        for arm in ('native','tehm'):
            first=root/'hosts/swe-agent'/arm/'episodes'/task
            receipt=first/'result.json'
            if not receipt.exists() or json.loads(receipt.read_text()).get('status') in {'RUNNING','NOT_STARTED',None}:
                raise ValueError('preserved pair must already be terminal')
    for task,arm in expected:
        if (root/'hosts/swe-agent'/arm/'episodes'/task).exists():
            raise ValueError('continuation refuses any previously started remaining episode')
    return remaining


def defer_episode(task,arm,status,reason):
    path=ROOT/'reports/deferred-episodes'/(task+'--'+arm+'.json')
    if path.exists():
        raise ValueError('episode already deferred; no implicit retry')
    record(path,{'task':task,'arm':arm,'status':status,'reason':reason,
        'strict_accepted':None,'measured_repair_outcome':False,'automatic_retry':False,
        'next':'separate retry round after original queue','created':time.time()})


def run(continuation=False):
    lock=ROOT/'reports/dispatcher.lock'
    with lock.open('a') as fd:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            _run(continuation)
        except BaseException as exc:
            record(ROOT/'reports/batch-state.json',{'status':'GLOBAL_BLOCKED',
                'reason':str(exc),'exception':type(exc).__name__,'updated':time.time()})
            raise


def _run(continuation=False):
    from run_swe import check_freeze
    registration=check_freeze()
    tasks=roster()
    if continuation:
        tasks=continuation_plan(ROOT,registration,tasks)
    elif (ROOT/'inputs/active-registration.json').exists():
        raise ValueError('re-frozen runtime requires explicit continue command')
    skip=interruption_policy(ROOT)=='skip_local_interruptions_no_retry'
    for index,task in enumerate(tasks,start=len(roster())-len(tasks)):
        check_freeze() # A runtime/permission/integrity defect is not a local interruption.
        if admission_blockers(ROOT):
            record(ROOT/'reports/batch-state.json',{'status':'PAUSED_UNKNOWN','task_index':index,'updated':time.time()})
            return
        path=ROOT/'qualification'/task['instance_id']/'qualification.json'
        record(ROOT/'reports/batch-state.json',{'status':'WAITING_FOR_QUALIFICATION',
               'task':task['instance_id'],'task_index':index,'updated':time.time()})
        while True:
            if admission_blockers(ROOT):
                record(ROOT/'reports/batch-state.json',{'status':'PAUSED_ADMISSION',
                       'task':task['instance_id'],'updated':time.time()})
                summary();return
            qualification=json.loads(path.read_text()) if path.exists() else None
            if qualification and qualification['status']=='PASS':
                break
            if qualification and skip:
                for arm in ('native','tehm'):
                    defer_episode(task['instance_id'],arm,'QUALIFICATION_BLOCKED',qualification.get('reason'))
                summary()
                break
            if qualification:
                record(ROOT/'reports/batch-state.json',{'status':'WAITING_FOR_QUALIFICATION_REPAIR',
                       'task':task['instance_id'],'reason':qualification.get('reason'),
                       'updated':time.time(),'paid_requests_while_waiting':0})
            time.sleep(10)
        if qualification['status']!='PASS':
            continue
        running=[]
        for arm in ('native','tehm'):
            directory=ROOT/'hosts/swe-agent'/arm/'episodes'/task['instance_id']
            if directory.exists():
                raise RuntimeError('episode exists; no rerun allowed: '+str(directory))
            logs=ROOT/'reports/worker-logs';logs.mkdir(exist_ok=True)
            out=(logs/(task['instance_id']+'--'+arm+'.log')).open('x')
            proc=subprocess.Popen([str(PYTHON),str(ROOT/'adapters/run_swe.py'),
                                   '--task',task['instance_id'],'--arm',arm],
                                  stdout=out,stderr=subprocess.STDOUT,cwd=PREP/'sources/SWE-agent')
            running.append((arm,proc,out))
        record(ROOT/'reports/batch-state.json',{'status':'RUNNING','task':task['instance_id'],
            'task_index':index,'workers':{a:p.pid for a,p,_ in running},'updated':time.time()})
        for arm,proc,out in running:
            proc.wait();out.close()
            episode=ROOT/'hosts/swe-agent'/arm/'episodes'/task['instance_id']
            if not (episode/'result.json').exists():
                record(episode/'controller-crash.json',{'task':task['instance_id'],'arm':arm,
                    'status':'WORKER_CRASH','returncode':proc.returncode,'paper_data':True,
                    'strict_accepted':None,'reason':'worker exited without a terminal receipt; usage not inferred'})
        rows=summary()
        pair=[r for r in rows if r['task']==task['instance_id']]
        check_freeze()
        if admission_blockers(ROOT):
            record(ROOT/'reports/batch-state.json',{'status':'GLOBAL_BLOCKED','task':task['instance_id'],
                'reason':'global admission or identity fault','updated':time.time()})
            return
        if any(r['status'] not in {'ACCEPTED','NOT_ACCEPTED'} for r in pair):
            if skip:
                for row in pair:
                    if row['status'] not in {'ACCEPTED','NOT_ACCEPTED'}:
                        defer_episode(row['task'],row['arm'],row['status'],row.get('reason'))
                summary()
                continue
            record(ROOT/'reports/batch-state.json',{'status':'PAUSED_TECHNICAL','task':task['instance_id'],
                'pair_statuses':{r['arm']:r['status'] for r in pair},'updated':time.time()})
            return
    rows=summary()
    incomplete=sum(r['status'] not in {'ACCEPTED','NOT_ACCEPTED'} for r in rows)
    record(ROOT/'reports/batch-state.json',{'status':'COMPLETE_WITH_INTERRUPTIONS' if incomplete else 'COMPLETE',
        'registered_episodes':40,'incomplete':incomplete,'automatic_retries':0,'updated':time.time()})


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('command',choices=['freeze','run','continue','summary']);a=p.parse_args()
    {'freeze':freeze,'run':run,'continue':lambda:run(True),'summary':summary}[a.command]()
