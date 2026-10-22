#!/usr/bin/env python3
"""Explicit, hash-bound continuation of a pre-model initial-reference stop.

The failed initial flow is reused and remains charged. No successful episode is
repeated, no input/knob/cap changes, and no native PDN action is hard-coded here.
An operator waits for the old dispatcher and workers to drain before installing
the registered shared delta. Original code, registrations and raw stop survive.
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

sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1])]
from t2_nb_firstlook import load,save,sha

ERROR='initial reference: floorplan not successfully measured'
TARGET='C00584__eth_frame_parser__ethernet-rx-gateway-asic__initial__NB__r1'


def check_initial_checkpoint(project):
    project=Path(project);e=load(project/'episode.json')
    if (e.get('status')!='UNVERIFIED_OR_SAFETY_STOP' or e.get('arm')!='NB'
            or e.get('error')!=ERROR or e.get('agent_run') or e.get('repair_turns')
            or not e.get('accounting',{}).get('passed')
            or e['accounting'].get('llm_requests')!=0):
        raise ValueError('Not the registered pre-model initial-reference checkpoint')
    if len(list((project/'backend').glob('RUN_*')))!=1:
        raise ValueError('Initial checkpoint must contain exactly one original flow')
    if sha(project/'constraints/config.mk')!=load(project/'manifest.json')['initial_config_sha256']:
        raise ValueError('Initial checkpoint configuration changed')
    return e


def never_started(jobs):
    pending=[]
    for row in jobs:
        project=Path(row['root'])/'episodes'/(row['task'].replace('/','__')+'__'+row['arm']+'__r1')
        if not project.exists():pending.append(row);continue
        if not (project/'episode.json').exists():raise ValueError('partial episode is not a never-started job')
        e=load(project/'episode.json')
        if e.get('status') not in ('ACCEPTED','NOT_ACCEPTED'):
            raise ValueError('unresolved episode blocks continuation: '+str(project))
    return pending


def integrate_batch(text):
    from t2_agent_nb_package import replace_once as replace
    text=replace(text,"def episode(root,task,arm,repeat,split='TEST'):",
                 "def episode(root,task,arm,repeat,split='TEST',resume_initial=False):")
    start="    project=root/'episodes'/name;project.mkdir(parents=True,exist_ok=False)\n"
    end="    H=Q.module('t2a_formal_harness',root/'code/memory/scripts/r2g_memory_eval.py')\n"
    if text.count(start)!=1 or text.count(end)!=1:raise ValueError('Unexpected checkpoint setup boundary')
    a,b=text.index(start),text.index(end)
    setup=text[a:b]
    marker="    # Fresh initial flow, never a copied qualification backend or workspace.\n"
    locked=setup.index(marker)
    copyblock=setup[locked:]
    prefix=("    project=root/'episodes'/name\n"
            "    prior_record=None\n"
            "    if resume_initial:\n"
            "        from t2_freepdk45_recovery import check_initial_checkpoint\n"
            "        prior_record=check_initial_checkpoint(project)\n"
            "    else:project.mkdir(parents=True,exist_ok=False)\n"
            "    lock=(project/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
            "    if not resume_initial:\n")
    prefix+=''.join('    '+line if line.strip() else line for line in copyblock.splitlines(True))
    prefix+=("    else:\n"
             "        cfg=project/'constraints/config.mk';man=json.loads((project/'manifest.json').read_text())\n")
    text=text[:a]+prefix+text[b:]
    text=replace(text,"    Q.save(project/'episode.json',record)\n    T.event(H,project,'attempt_start',",
        "    if prior_record:\n"
        "        record['started']=prior_record['started']\n"
        "        record['initial_continuation']={'original_error':prior_record['error'],"
        "'checkpoint_reused':True,'initial_flow_repeated':False,'resume_started':start}\n"
        "    Q.save(project/'episode.json',record)\n"
        "    T.event(H,project,'attempt_resume' if resume_initial else 'attempt_start',")
    flow="        rc=H.run(['bash',H.FLOW/'run_orfs.sh',project,H.CFG['platform']],env,project/'eval_arm.log',deadline_timeout(H,project))\n"
    text=replace(text,flow,"        if not resume_initial:\n    "+flow+
        "        else:\n            rc=2 # original, charged PDN failure; never re-launch its initial flow\n")
    before="        record['terminal_reason']=('accounting_failure'"
    after=("        if prior_record:\n"
           "            record['total_worker_wall_s']+=prior_record.get('total_worker_wall_s',0)\n"
           "            record['harness_cpu_s']+=prior_record.get('harness_cpu_s',0)\n"
           "            record['wall_s']+=prior_record.get('wall_s',0)\n"
           "            record['paused_wall_s']=max(0,start-prior_record.get('ended',start))\n"
           "            record['elapsed_including_pause_s']=record['ended']-record['started']\n"
           "        record['terminal_reason']=('accounting_failure'")
    text=replace(text,before,after)
    compile(text,'registered_initial_recovery_worker','exec')
    return text


def run_initial(root,revision,slots):
    """One explicit continuation, with the original initial flow and ledger."""
    nb=Path(root)/'nb';revision=Path(revision)
    cmd=['taskset','-c',','.join(map(str,slots[0])),sys.executable,
         str(nb/'code/memory/scripts/t2_freepdk45_recovery.py'),'recover','--root',str(nb),
         '--revision',str(revision)]
    with (revision/'checkpoint-worker.log').open('x') as log:
        p=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        while p.poll() is None:
            save(Path(root)/'dispatch-state.json',{'status':'RECOVERING_INITIAL_CHECKPOINT',
                'running':[{'pid':p.pid,'episode':TARGET}],'revision':str(revision),'updated':time.time()})
            time.sleep(10)
    if p.returncode:raise RuntimeError('Initial checkpoint worker failed')
    e=load(nb/'episodes'/TARGET/'episode.json')
    if e.get('status') not in ('ACCEPTED','NOT_ACCEPTED') or not e.get('accounting',{}).get('passed'):
        raise RuntimeError('Initial checkpoint remains technically unresolved')
    save(revision/'checkpoint-completed.json',{'episode':TARGET,'status':e['status'],
        'initial_flow_repeated':False,'accounting':e['accounting'],'costs_include_original_prefix':True})


def validate_continuation(root,revision):
    root,revision=Path(root).resolve(),Path(revision).resolve()
    applied=load(revision/'applied.json')
    if applied['root']!=str(root) or applied['registration_sha256']!=sha(root/'registration.json'):
        raise ValueError('Unregistered continuation runtime')
    for p,h in applied['files'].items():
        if sha(p)!=h:raise ValueError('Continuation file changed: '+p)
    with (revision/'resume-launch.json').open('x') as f:
        json.dump({'started':time.time(),'pid':os.getpid(),'registration_sha256':applied['registration_sha256']},f)


def prepare(root,revision,tests):
    root,revision=Path(root).resolve(),Path(revision).resolve()
    if revision.exists():raise FileExistsError('No overwrite of a recovery revision')
    import xml.etree.ElementTree as ET
    cases=list(ET.parse(tests).getroot().iter('testcase'))
    if len(cases)<25 or any(c.find('failure') is not None or c.find('error') is not None for c in cases):
        raise ValueError('Passing recovery regression required')
    import t2_freepdk45_main as M
    M.F.verify_files(load(root/'registration.json')['files'])
    check_initial_checkpoint(root/'nb/episodes'/TARGET)
    revision.mkdir(parents=True)
    shutil.copy2(tests,revision/'offline-tests.xml')
    sources={}
    for base in (root/'code/memory/scripts',root/'nb/code/memory/scripts'):
        for name in ('t2_nb_firstlook.py','t2_freepdk45_main.py','t2_freepdk45_recovery.py','t2a_batch.py'):
            dest=base/name
            content=integrate_batch(dest.read_text()) if name=='t2a_batch.py' else (Path(__file__).parent/name).read_text()
            staged=revision/'staged'/dest.relative_to(root);staged.parent.mkdir(parents=True,exist_ok=True)
            staged.write_text(content);compile(content,str(dest),'exec')
            sources[str(dest)]={'staged':str(staged),'sha256':sha(staged),
                               'previous_sha256':sha(dest) if dest.exists() else None}
    registration={'root':str(root),'created':time.time(),'files':sources,
        'operator_source_sha256':sha(__file__),
        'base_registration_sha256':sha(root/'registration.json'),
        'base_nb_registration_sha256':sha(root/'nb/registration.json'),
        'user_text':'修复一下，继续运行','checkpoint':TARGET,
        'scope':'shared initial geometry evidence guard; reuse exactly one pre-model checkpoint; never-started jobs only',
        'unchanged':['RTL','SDC','5ns','initial config','25-knob permissions','area caps','three total online requests',
                     'no cumulative cost cap','memory methods','native PDN configuration','strict acceptance'],
        'no_completed_reruns':True,'raw_stop_archived':True,'wait_for_old_workers':True}
    save(revision/'registration.json',registration)
    save(revision/'operator.json',{'status':'PREPARED','root':str(root)})


def operate(root,revision):
    root,revision=Path(root).resolve(),Path(revision).resolve()
    grant=load(revision/'registration.json')
    if grant['root']!=str(root):raise ValueError('Wrong recovery root')
    if sha(__file__)!=grant['operator_source_sha256']:raise ValueError('Operator changed after registration')
    lease=(root/'supervisor.lock').open('a')
    # Never patch code beneath a live old worker. The old dispatcher already
    # refuses new admissions, and exits after its existing jobs naturally drain.
    while True:
        running=[str(p) for p in (root/'nb/episodes').glob('*/episode.json') if load(p).get('status')=='RUNNING']
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
        except BlockingIOError:locked=False
        if locked and not running:break
        if locked:fcntl.flock(lease,fcntl.LOCK_UN)
        save(revision/'operator.json',{'status':'WAITING_FOR_OLD_WORKERS_TO_DRAIN','running':running,'updated':time.time()})
        time.sleep(15)
    try:
        import t2_freepdk45_main as M
        reg=load(root/'registration.json');M.F.verify_files(reg['files'])
        if sha(root/'registration.json')!=grant['base_registration_sha256']:raise ValueError('Base registration changed')
        if sha(root/'nb/registration.json')!=grant['base_nb_registration_sha256']:raise ValueError('NB registration changed')
        project=root/'nb/episodes'/TARGET;check_initial_checkpoint(project)
        original={}
        for rel in ('episode.json','reports/resource_ledger.jsonl'):
            source=project/rel;dest=revision/'checkpoint-original'/rel
            dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,dest)
            original[str(source)]={'sha256':sha(source),'bytes':source.stat().st_size,'archive':str(dest)}
        for rel in ('registration.json','nb/registration.json','dispatch-state.json','nb/dispatch-state.json'):
            dest=revision/'original'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/rel,dest)
        for dest,change in grant['files'].items():
            path=Path(dest)
            if (sha(path) if path.exists() else None)!=change['previous_sha256']:raise ValueError('Runtime drift: '+dest)
            if sha(change['staged'])!=change['sha256']:raise ValueError('Staged delta changed: '+dest)
        for dest,change in grant['files'].items():
            path=Path(dest)
            if path.exists():
                archive=revision/'original'/path.relative_to(root);archive.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,archive)
            shutil.copy2(change['staged'],path)
        changes={p:d['sha256'] for p,d in grant['files'].items()}
        nbreg=load(root/'nb/registration.json')
        nbreg['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'nb/code')})
        nbreg['shared_geometry_delta']={'revision':str(revision),'registration_sha256':sha(revision/'registration.json')}
        save(root/'nb/registration.json',nbreg)
        reg['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'code')})
        reg['files'][str(root/'nb/registration.json')]=sha(root/'nb/registration.json')
        reg['shared_geometry_delta']=nbreg['shared_geometry_delta'];save(root/'registration.json',reg)
        M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
        # Independently re-read the real frozen native substage, before any paid
        # request or repair. The old initial flow is not re-executed.
        from importlib.util import spec_from_file_location,module_from_spec
        spec=spec_from_file_location('geometry_delta',root/'nb/code/memory/scripts/t2_nb_firstlook.py')
        module=module_from_spec(spec);spec.loader.exec_module(module)
        bound=module.resolve_area_reference(load(project/'manifest.json'))
        save(revision/'native-geometry-check.json',{'status':'PASS','areas':{k:v['ref'] for k,v in bound['area'].items()},
             'reference_receipt':str(project/'reports/initial-area-reference.json'),
             'zero_new_flows':True,'zero_model_calls':True})
        save(revision/'applied.json',{'root':str(root),'registration_sha256':sha(root/'registration.json'),
            'files':changes,'original_checkpoint':original,'applied':time.time()})
        save(revision/'operator.json',{'status':'DELTA_VERIFIED_STARTING_CONTINUATION','updated':time.time()})
    finally:fcntl.flock(lease,fcntl.LOCK_UN);lease.close()
    os.execv(sys.executable,[sys.executable,str(root/'code/memory/scripts/t2_freepdk45_main.py'),
        'run','--root',str(root),'--continuation',str(revision)])


def recover(nb,revision):
    import t2_agent_request_resume as R
    R.install_online()
    import t2a_batch as B
    nb,revision=Path(nb).resolve(),Path(revision).resolve()
    project=nb/'episodes'/TARGET;check_initial_checkpoint(project)
    original=load(revision/'applied.json')['original_checkpoint']
    for p,entry in original.items():
        if sha(p)!=entry['sha256']:raise ValueError('Original checkpoint changed before resume')
    task=load(project/'episode.json')['task']
    B.episode(nb,task,'NB',1,'TEST',resume_initial=True)
    ledger=project/'reports/resource_ledger.jsonl';entry=original[str(ledger)]
    if hashlib.sha256(ledger.read_bytes()[:entry['bytes']]).hexdigest()!=entry['sha256']:
        raise ValueError('Original ledger prefix changed')


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('prepare','operate','recover'))
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--revision',type=Path,required=True)
    ap.add_argument('--tests',type=Path);a=ap.parse_args()
    if a.command=='prepare':prepare(a.root,a.revision,a.tests)
    elif a.command=='operate':operate(a.root,a.revision)
    else:recover(a.root,a.revision)


if __name__=='__main__':main()
