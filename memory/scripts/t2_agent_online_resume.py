#!/usr/bin/env python3
"""Append-only continuation of never-started agent9 episodes after a JSON 400.

No new ledger, cost reset, reconstruction or retry of a started cell. UNKNOWN
occupancy requires an independent explicit user authorisation before launch.
"""
from __future__ import annotations
import argparse
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

CODE=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(CODE/'memory/scripts'),str(CODE/'memory')]
import t2_agent_request_resume as R

SCHEMA='agent9-json-mode-never-started-continuation-v1'
sha,load,save,rows=R.sha,R.load,R.save,R.rows


def pending_jobs(base):
    jobs=load(base/'frozen-memory-plan.json')['jobs'];pending=[];kept=[];seen=set()
    for job in jobs:
        key=(job['task'],job['arm'])
        if key in seen:raise ValueError('Duplicate registered episode')
        seen.add(key)
        root=Path(job['root'])
        if root.resolve().parent!=(base/'folds').resolve():raise ValueError('Foreign fold in plan')
        name=job['task'].replace('/','__')+'__'+job['arm']+'__r1'
        (kept if (root/'episodes'/name).exists() else pending).append(job)
    return pending,kept


def ledger_module(code):
    p=code/'r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    spec=importlib.util.spec_from_file_location('json_resume_resource_ledger',p)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def incident(base):
    phase=base/'online-phase/resource_ledger.jsonl'
    unknown=[e for e in rows(phase) if e.get('type')=='budget_settle'
             and any(v=='UNKNOWN' for v in e.get('actual',{}).values())]
    if len(unknown)!=1:raise ValueError('Expected exactly the reviewed UNKNOWN request')
    settle=unknown[0];matches=[]
    for f in (base/'folds').glob('*/episodes/*/reports/resource_ledger.jsonl'):
        for e in rows(f):
            if e.get('type')=='llm_request' and e.get('phase_hold_id')==settle['hold_id']:
                matches.append((f,e))
    if len(matches)!=1:raise ValueError('UNKNOWN ownership is not unique')
    local,request=matches[0];project=local.parent.parent
    if request.get('status')!=400 or request.get('role')!='auxiliary':
        raise ValueError('Not the reviewed JSON-mode HTTP 400')
    response=project/'amem-query-1.try0.response.json';body=project/'amem-query-1.try0.request.json'
    message=load(response).get('error',{}).get('message','')
    if "Prompt must contain the word 'json'" not in message:raise ValueError('Different API failure')
    L=ledger_module(project.parent.parent/'code')
    bounds={k:v for k,v in settle['booked'].items() if settle['actual'].get(k)=='UNKNOWN'}
    targets=[{'ledger_path':str(phase.resolve()),'hold_id':settle['hold_id'],'upper_bound':bounds},
             {'ledger_path':str(local.resolve()),'hold_id':request['hold_id'],'upper_bound':bounds}]
    for t in targets:L.validate_occupancy(rows(t['ledger_path']),t['hold_id'],t['upper_bound'])
    return {'targets':targets,'request':str(body),'request_sha256':sha(body),
            'response':str(response),'response_sha256':sha(response),
            'actual_usage':'UNKNOWN','no_retry_of_this_request':True}


def validate_authorization(path,expected):
    grant=load(path)
    key=lambda t:(t['ledger_path'],t['hold_id'])
    if (grant.get('schema')!='r2g-unknown-occupancy-authorization-v1'
            or grant.get('user_approved') is not True or not grant.get('user_text')
            or sorted(grant.get('targets',[]),key=key)!=sorted(expected['targets'],key=key)):
        raise ValueError('Explicit UNKNOWN occupancy authorisation required for both exact holds')
    return grant


def prepare(base,revision,tests):
    import t2_nb_followup as F
    if revision.exists():raise ValueError('No overwrite of a continuation')
    state=load(base/'dispatch-state.json')
    if state.get('status')!='PAUSED' or state.get('running'):raise ValueError('Old queue must be fully drained')
    cases=list(ET.parse(tests).getroot().iter('testcase'))
    if len(cases)<20 or any(c.find(x) is not None for c in cases for x in ('failure','error','skipped')):
        raise ValueError('Passing transport/continuation regression required')
    jobs,kept=pending_jobs(base)
    if len(jobs)!=56 or len(kept)!=8:raise ValueError('Scope changed: expected 56 new and 8 retained cells')
    q=load(base/'queue-registration.json');F.verify_files(q['files']);F.verify_files(q['external_inputs'])
    for f in (base/'folds').glob('*/registration.json'):F.verify_files(load(f)['files'])
    evidence=incident(base)
    protected,prefixes,_=R.preserved(base)
    revision.mkdir(parents=True);(revision/'folds').mkdir();(revision/'logs').mkdir()
    source=Path(kept[0]['root'])/'code';shutil.copytree(source,revision/'code',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    entry=revision/'code/memory/scripts'/Path(__file__).name;shutil.copy2(__file__,entry)
    request_code=revision/'code/memory/scripts/t2_agent_request_resume.py'
    shutil.copy2(Path(__file__).with_name('t2_agent_request_resume.py'),request_code)
    shutil.copy2(tests,revision/'offline-tests.xml')
    save(revision/'prior-dispatch-state.json',state);save(revision/'incident.json',evidence)
    save(revision/'protected-artifacts.json',protected);save(revision/'ledger-prefixes.json',prefixes)
    save(revision/'authorization.json',{'user_text':'优化一下继续实验','change_scope':'JSON-mode transport compatibility; never-started cells only',
        'occupancy_authorization_required_separately':True,'same_phase_ledger':str(base/'online-phase/resource_ledger.jsonl'),
        'old_cells_preserved':kept,'new_cells':56,'construction_reused':40,'implicit_retries':0,
        'total_online_requests_per_episode':3,'cumulative_cost_cap':None})
    shared_files={str(revision/n):sha(revision/n) for n in ('offline-tests.xml','prior-dispatch-state.json','incident.json',
        'protected-artifacts.json','ledger-prefixes.json','authorization.json')}
    shared_files.update(F.frozen(revision/'code'))
    by_id={Path(j['root']).name for j in jobs};new_jobs=[]
    for old in sorted((base/'folds').iterdir()):
        fold=revision/'folds'/old.name
        if old.name not in by_id:
            fold.symlink_to(old,target_is_directory=True);continue # Read-only view of retained results.
        if (old/'episodes').exists():raise ValueError('Pending fold already has episode artifacts')
        fold.mkdir();shutil.copytree(old/'code',fold/'code',ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
        new_entry=fold/'code/memory/scripts/t2_agent_request_resume.py';shutil.copy2(request_code,new_entry)
        for child in old.iterdir():
            if child.name not in ('code','registration.json'): (fold/child.name).symlink_to(child,target_is_directory=child.is_dir())
        reg=load(old/'registration.json');reg['online_policy']=R.policy();reg['online_entrypoint']=str(new_entry)
        reg['transport_delta']={'policy':R.JSON_MODE_POLICY,'base_registration':str(old/'registration.json'),
            'base_registration_sha256':sha(old/'registration.json'),'no_old_episode_retry':True}
        reg['files'].update(shared_files);reg['files'].update(F.frozen(fold/'code'))
        save(fold/'registration.json',reg)
    for job in jobs:new_jobs.append({**job,'root':str(revision/'folds'/Path(job['root']).name)})
    files=dict(shared_files)
    for hid in by_id:
        f=revision/'folds'/hid/'registration.json';files[str(f)]=sha(f);files.update(F.frozen(f.parent/'code'))
    save(revision/'registration.json',{'schema':SCHEMA,'base_queue':str(base),'files':files,'jobs':new_jobs,
        'concurrency':8,'request_policy':R.policy(),'requires_explicit_occupancy':True,'created':time.time()})
    save(revision/'queue-registration.json',{**q,'files':files,'continuation_of':str(base),'new_episode_count':56})
    reg=load(revision/'registration.json')
    reg['files'][str(revision/'queue-registration.json')]=sha(revision/'queue-registration.json')
    save(revision/'registration.json',reg)
    save(revision/'dispatch-state.json',{'status':'FROZEN_WAITING_UNKNOWN_AUTHORIZATION','pending':56,'running':[],
        'old_cells_retained':8,'registration_sha256':sha(revision/'registration.json')})
    R.install_online()
    import t2a_batch as B
    for hid in by_id:B.preflight(revision/'folds'/hid,'TEST')
    R.invariant(base,revision)
    print(json.dumps(load(revision/'dispatch-state.json')))


def occupy(revision,authorization):
    expected=load(revision/'incident.json');validate_authorization(authorization,expected)
    L=ledger_module(revision/'code')
    for t in expected['targets']:
        ev=rows(t['ledger_path']);L.validate_occupancy(ev,t['hold_id'],t['upper_bound'])
        if any(e.get('type')=='budget_unknown_occupancy' and e.get('hold_id')==t['hold_id'] for e in ev):
            raise ValueError('Occupancy already exists: no implicit replay')
    for t in expected['targets']:
        p=Path(t['ledger_path']);project=p.parent.parent
        cmd=[sys.executable,str(revision/'code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'),
             'occupy-unknown',str(project),'--hold-id',t['hold_id'],'--authorization',str(authorization.resolve()),
             '--authorization-sha256',sha(authorization)]
        subprocess.run(cmd,env={**os.environ,'R2G_LEDGER_PATH':str(p),'R2G_BUDGET_FILE':str(Path(load(revision/'registration.json')['base_queue'])/'online-budget.json')},check=True)
        # Validate using the original fail-closed rule, not by resetting the bank.
        spent=L.budget_totals(rows(p))
        if any(spent[k]=='UNKNOWN' for k in ('tokens_in','tokens_out')):raise ValueError('Other UNKNOWN remains')


def run(revision,authorization):
    import t2_agent_followup as G
    import t2_nb_followup as F
    reg=load(revision/'registration.json');base=Path(reg['base_queue'])
    if Path(__file__).resolve()!=revision/'code/memory/scripts'/Path(__file__).name:raise ValueError('Use frozen controller')
    lock=(base/'supervisor.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    R.invariant(base,revision);validate_authorization(authorization,load(revision/'incident.json'))
    # Before any admission/occupancy, confirm that every scheduled cell is still new.
    pending,kept=pending_jobs(base)
    if len(pending)!=56 or len(kept)!=8:raise ValueError('Original queue changed')
    for job in reg['jobs']:
        p=Path(job['root'])/'episodes'/(job['task'].replace('/','__')+'__'+job['arm']+'__r1')
        if p.exists():raise ValueError('Existing cell: no implicit retry')
    with (revision/'resume-start.json').open('x') as f:
        json.dump({'started':time.time(),'registration_sha256':sha(revision/'registration.json'),
                   'occupancy_authorization':str(authorization),'authorization_sha256':sha(authorization)},f)
    occupy(revision,authorization)
    q=load(revision/'queue-registration.json');G.configure_reuse();R.install_online()
    os.environ.update(R2G_EMBEDDING_PYTHON=q['embedding_python'],R2G_EMBEDDING_LIBRARY_PATH=q['embedding_library_path'])
    R.invariant(base,revision)
    save(base/'dispatch-state.json',{'status':'CONTINUED_IN_NEW_REVISION','revision':str(revision),
        'preserved_technical_cells':8,'newly_dispatchable':56,'updated':time.time()})
    def popen(cmd,**kwargs):
        cmd=list(cmd);p=Path(cmd[4])
        if p.name!='t2a_batch.py' or cmd[5]!='episode':raise ValueError('Unexpected worker command')
        cmd[4]=str(p.with_name('t2_agent_request_resume.py'));return subprocess.Popen(cmd,**kwargs)
    try:
        state=F.dispatch(revision,reg['jobs'],popen=popen)
        R.invariant(base,revision)
        save(base/'dispatch-state.json',{'status':'CONTINUED_IN_NEW_REVISION','revision':str(revision),
            'continuation_status':state['status'],'updated':time.time()})
    except Exception as e:
        save(revision/'dispatch-state.json',{'status':'PAUSED_TECHNICAL','reason':str(e),'updated':time.time()});raise


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('command',choices=('prepare','run'))
    ap.add_argument('--base',type=Path);ap.add_argument('--revision',type=Path,required=True)
    ap.add_argument('--tests',type=Path);ap.add_argument('--occupancy-authorization',type=Path)
    a=ap.parse_args()
    if a.command=='prepare':prepare(a.base.resolve(),a.revision.resolve(),a.tests.resolve())
    else:
        if a.occupancy_authorization is None:ap.error('Explicit UNKNOWN occupancy authorization required')
        run(a.revision.resolve(),a.occupancy_authorization.resolve())
