"""Prepare/freeze the correction; launch is a separate explicit operation.

No prior record, registration, sealed memory or running worker is rewritten.
"""
import argparse
import collections
import copy
import hashlib
import importlib.util
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
PARENT=ROOT.parent
BASE=PARENT.parent/'t2-freepdk45-main50-adamem-20261022-r3'
PRIOR=PARENT/'namespace-v3'
VARIANTS=('RECIPE','TEHM_FIXED','TEHM_SELECTOR')
PYTHON='/data1/zhangdy/EDACode/.venv/bin/python'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def load(path):return json.loads(Path(path).read_text())
def record(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
    temp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n');temp.replace(path)
def unit_sha(unit):
    return hashlib.sha256(json.dumps(unit,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def prepare():
    if (ROOT/'plan.json').exists():raise ValueError('Never overwrite a prepared revision')
    # Parse the exact tool declarations bound by BOTH the source NB run and the
    # original task contracts. Do not substitute another local ORFS checkout.
    import yaml
    nb_registration=BASE/'nb/registration.json'
    nb=load(nb_registration)
    matches=[(Path(p),h) for p,h in nb['files'].items() if p.endswith('/flow/scripts/variables.yaml')]
    if len(matches)!=1:raise ValueError('Original NB defaults authority is ambiguous')
    variables,expected=matches[0]
    if sha(variables)!=expected:raise ValueError('Original ORFS variables have changed')
    definitions=yaml.safe_load(variables.read_text())
    whitelist=('ROUTING_LAYER_ADJUSTMENT','GPL_ROUTABILITY_DRIVEN',
               'SKIP_ANTENNA_REPAIR','MAX_REPAIR_ANTENNAS_ITER_DRT')
    defaults={}
    for knob in whitelist:
        value=definitions[knob]['default']
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
            raise ValueError('Default is not a literal numeric declaration: '+knob)
        defaults[knob]=value
    baseline=load(PARENT/'baseline-reference.json')
    baseline_by_design={r['design']:Path(r['episode']).parent for r in baseline['rows']}
    units={};bindings={};unbound=[]
    original_plan=load(PARENT/'plan.json')
    for job in original_plan['jobs']:
        if job['variant']!='TEHM_FIXED':continue
        old=Path(job['root'])
        if load(old/'registration.json')['files'].get(str(variables))!=expected:
            raise ValueError('Current task contract has a different toolchain default')
        for line in (old/'shared/shared_units.jsonl').read_text().splitlines():
            u=json.loads(line);uid=u['unit_id']
            if uid in units and unit_sha(units[uid])!=unit_sha(u):
                raise ValueError('Conflicting shared copies: '+uid)
            units[uid]=u
    source_files={str(variables):expected,str(nb_registration):sha(nb_registration)}
    for uid,u in units.items():
        owner=baseline_by_design.get(u.get('source_handoff_id'))
        sources=(u.get('provenance') or {}).get('sources') or []
        paths=[Path(s['path']) for s in sources]
        if not owner or not paths or not all(p.is_file() and p.resolve().is_relative_to(owner.resolve()) for p in paths):
            unbound.append(uid);continue
        evidence={str(p):sha(p) for p in paths}
        source_files.update(evidence)
        bindings[uid]={'unit_sha256':unit_sha(u),'source_registration':str(nb_registration),
            'source_registration_sha256':sha(nb_registration),'variables_sha256':expected,
            'source_episode':str(owner),'raw_sources':evidence}
    registry={'schema':'figure2-effective-defaults-v1','variables_file':str(variables),
        'variables_sha256':expected,'defaults':defaults,'historical_units':bindings,
        'unbound_units_remain_unknown':unbound,'admission_or_annotation_changed':False}
    record(ROOT/'code/memory/scripts/figure2-effective-defaults.json',registry)
    carried=[];jobs=[]
    for job in original_plan['jobs']:
        if (Path(job['root'])/'episodes').exists():
            carried.append(job);continue
        destination=ROOT/'jobs'/job['design']/job['variant'];destination.mkdir(parents=True)
        old=Path(job['root'])
        for name in ('code','shared','source','ann','tehm-import','agent-store','store','controls'):
            (destination/name).symlink_to(ROOT/'code' if name=='code' else old/name,target_is_directory=True)
        for name in ('exclusions.json','verification.json','overrides.json','evidence-index.json',
                     'tehm-v2-policy.json','online-input-authorization.json','episode-budget.json'):
            (destination/name).symlink_to(old/name)
        reg=copy.deepcopy(load(old/'registration.json'))
        reg.update(figure2_revision='v4',shared_worker_file=str(ROOT/'code/memory/scripts/t2a_batch.py'),
            revision_protocol=str(ROOT/'PROTOCOL.md'),selector_scope='local_execution_only',
            source_cell_registration=str(old/'registration.json'),paper_data=False)
        reg['files'].update(source_files)
        reg['files'][str(old/'registration.json')]=sha(old/'registration.json')
        record(destination/'registration.json',reg)
        jobs.append({**job,'root':str(destination),'source_root':str(old)})
    if len(carried)+len(jobs)!=150:raise ValueError('Original denominator changed')
    (ROOT/'baseline-reference.json').symlink_to(PARENT/'baseline-reference.json')
    plan={'schema':'figure2-v4-correction','jobs':jobs,'carried_cells':carried,
        'registered_original_cells':150,'reused_R2G':50,'repeat':1,
        'new_online_requests_max':3*sum(j['variant']!='RECIPE' for j in jobs),
        'slots':original_plan['slots'],'local_actions_per_episode':3,
        'online_requests_per_TEHM_episode':3,'RECIPE_online_requests':0,
        'test_time_writeback':False,'launch_requested_this_turn':False}
    record(ROOT/'plan.json',plan)
    record(ROOT/'reports/preparation.json',{'known_defaults':defaults,'bound_history_units':len(bindings),
        'unbound_history_units':len(unbound),'pending_cells':len(jobs),'carried_cells':len(carried),
        'new_eda_flows':0,'new_model_requests':0,'labels_changed':0})
    record(ROOT/'reports/prior-dispatch-hold.json',{'reason':'user_requested_correction_and_refreeze',
        'dispatcher_only_SIGSTOP':load(PRIOR/'launch.json')['lanes'],
        'prior_worker_states':{v:load(PRIOR/(v+'-state.json')) for v in VARIANTS},
        'running_workers_not_signalled':True,'other_experiments_untouched':True,'created':time.time()})
    print(json.dumps({'prepared':len(jobs),'carried':len(carried),'defaults':defaults}))


def freeze():
    if (ROOT/'registration.json').exists():raise ValueError('Never replace a freeze')
    cases=list(ET.parse(ROOT/'reports/tests.xml').getroot().iter('testcase'))
    if not cases or any(c.find(k) is not None for c in cases for k in ('failure','error','skipped')):
        raise ValueError('Correction tests must pass')
    frozen={str(p):sha(p) for folder in ('code','tests') for p in (ROOT/folder).rglob('*')
            if p.is_file() and not any(t in p.parts for t in ('__pycache__','.pytest_cache'))}
    for name in ('experiment.py','worker.py','figure2_policy.py','replay.py','PROTOCOL.md','plan.json','baseline-reference.json',
                 'reports/tests.xml','reports/red.xml','reports/replay.json','reports/preparation.json',
                 'reports/prior-dispatch-hold.json','reports/precondition-receipts.json'):
        frozen[str(ROOT/name)]=sha(ROOT/name)
    common=dict(frozen);plan=load(ROOT/'plan.json')
    for job in plan['jobs']:
        path=Path(job['root'])/'registration.json';reg=load(path)
        reg['files'].update(common);record(path,reg)
        for p,h in reg['files'].items():
            if p in frozen and frozen[p]!=h:raise ValueError('Hash conflict: '+p)
            frozen[p]=h
        frozen[str(path)]=sha(path)
    frozen[str(PARENT/'registration.json')]=sha(PARENT/'registration.json')
    frozen[str(PRIOR/'registration.json')]=sha(PRIOR/'registration.json')
    for path,digest in frozen.items():
        if sha(path)!=digest:raise ValueError('Frozen input changed: '+path)
    record(ROOT/'registration.json',{'schema':'figure2-v4-corrected-preconditions',
        'status':'FROZEN_READY_NOT_LAUNCHED','created':time.time(),'files':frozen,'plan':plan,
        'tests_passed':len(cases),'online_requests':0,'new_eda_flows':0,
        'boundary':'offline tests and real-record replay only; not real-flow validation',
        'selector_scope':'local_execution_only; advice and native safety withdrawal shared',
        'admission_or_source_labels_changed':False,'no_started_cell_rerun':True})
    print(json.dumps({'status':'FROZEN_READY_NOT_LAUNCHED','tests':len(cases),'sha256':sha(ROOT/'registration.json')}))


def bind_receipts():
    """Staging only: add independent pre-run execution-state authorities."""
    if (ROOT/'registration.json').exists():raise ValueError('Never change a frozen revision')
    path=ROOT/'code/memory/scripts/figure2-effective-defaults.json';registry=load(path)
    units={}
    for job in load(PARENT/'plan.json')['jobs']:
        if job['variant']=='TEHM_FIXED':
            for line in (Path(job['root'])/'shared/shared_units.jsonl').read_text().splitlines():
                u=json.loads(line);units[u['unit_id']]=u
    files={};count=0
    for uid,binding in registry['historical_units'].items():
        raw=units[uid].get('raw_row') or {};tag=raw.get('pre_run_tag')
        if not tag or Path(tag).name!=tag:continue
        receipt=Path(binding['source_episode'])/'backend'/tag/'effective-config.json'
        if not receipt.is_file():continue
        obj=load(receipt)
        if obj.get('status')!='PASS' or obj.get('errors'):continue
        ref={'path':str(receipt),'sha256':sha(receipt)}
        binding['pre_execution']=ref;files[str(receipt)]=ref['sha256'];count+=1
    record(path,registry)
    for job in load(ROOT/'plan.json')['jobs']:
        p=Path(job['root'])/'registration.json';reg=load(p);reg['files'].update(files);record(p,reg)
    record(ROOT/'reports/precondition-receipts.json',{'pre_run_receipts_bound':count,
        'implicit_default_without_execution_receipt':'UNKNOWN','files':files})
    print(json.dumps({'pre_run_receipts_bound':count}))


def validate(job=None):
    reg=load(ROOT/'registration.json')
    if reg['status']!='FROZEN_READY_NOT_LAUNCHED':raise ValueError('Unregistered revision')
    if (ROOT/'GLOBAL-STOP.json').exists():raise ValueError('Revision integrity stop')
    checks=reg['files']
    if job is not None:
        if str(job) not in {j['root'] for j in reg['plan']['jobs']}:raise ValueError('Not a pending registered cell')
        path=Path(job)/'registration.json'
        if sha(path)!=reg['files'][str(path)]:raise ValueError('Cell registration changed')
        checks=load(path)['files']
    for path,digest in checks.items():
        if sha(path)!=digest:raise ValueError('Frozen input changed: '+path)
    return reg


def prior_workers_ended():
    """Never overlap this revision with still-running old flow namespaces."""
    for variant in VARIANTS:
        state=load(PRIOR/(variant+'-state.json'))
        job=state.get('job') or {}
        if not job:continue
        episodes=list((Path(job['root'])/'episodes').glob('*/episode.json'))
        if not episodes or any(load(p).get('status')=='RUNNING' for p in episodes):return False
        pid=state.get('worker_pid')
        if pid and Path('/proc/'+str(pid)).exists():return False
    return True


def cost(row,project):
    spec=importlib.util.spec_from_file_location('figure2_prior_cost',PARENT/'experiment.py')
    old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
    return old.cost(row,project)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['prepare','bind_receipts','freeze','validate'])
    args=parser.parse_args();result=globals()[args.command]()
    if args.command=='validate':print(json.dumps({'status':'PASS','files':len(result['files'])}))
