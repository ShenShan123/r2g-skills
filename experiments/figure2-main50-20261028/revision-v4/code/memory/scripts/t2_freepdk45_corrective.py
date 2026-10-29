#!/usr/bin/env python3
"""One explicit shared-harness correction; retain completed data and all costs.

All 50 input configs are checked through native Make resolution BEFORE launch.
Known checker timeouts end an unmeasured cell, not the whole registered queue;
unexpected failures, unbound results and UNKNOWN provider usage still pause.
"""
from __future__ import annotations
import argparse
import copy
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

sys.path[:0]=[str(Path(__file__).parent),str(Path(__file__).parents[1])]
from t2_nb_firstlook import load,save,sha
import t2a_envelope as E

VERSION='freepdk45-shared-harness-correction-v1'
SCRIPTS=('t2_agent_request_resume.py','t2_agent_verdict.py','t2_nb_followup.py',
         't2_freepdk45_main.py','t2_signoff_integrity.py','t2_freepdk45_corrective.py',
         't2_agent_memories.py','t2a_adapters.py')


def uniform_initial(config):
    if str(config.get('CORE_UTILIZATION'))!='20':raise ValueError('Not the registered uniform 20 percent input')
    out=dict(config);out.pop('CORE_AREA',None);out.pop('DIE_AREA',None)
    return out


def integrate_batch(text):
    needle="        record['initial']=initial\n"
    if text.count(needle)!=1:raise ValueError('Unexpected initial verification boundary')
    text=text.replace(needle,needle+
        "        if initial.get('technical_signoff'):\n"
        "            raise T.TechnicalFailure('Initial signoff was not measured; no model repair of checker failure')\n",1)
    from t2_agent_nb_package import replace_once
    return replace_once(text,"    env.update(TEHM_EVIDENCE_CALIBER='main',TEHM_D1_REQUIRE_VERIFIED_POSITIVE='1')",
        "    env.update(TEHM_EVIDENCE_CALIBER='main',TEHM_D1_REQUIRE_VERIFIED_POSITIVE='1',\n"
        "               R2G_METER_STRICT_CHECKERS='1',LVS_CRASH_RETRIES='1')")


def remaining_jobs(jobs):
    import t2_freepdk45_main as M
    from t2_signoff_integrity import effective_episode
    pending=[]
    for row in jobs:
        p=Path(row['root'])/'episodes'/(row['task'].replace('/','__')+'__'+row['arm']+'__r1')
        if not p.exists():pending.append(row);continue
        if not (p/'episode.json').exists():raise ValueError('Partial episode cannot be retried')
        e=effective_episode(p)
        if e['status'] not in ('ACCEPTED','NOT_ACCEPTED') and not M.registered_checker_stop(p,e):
            raise ValueError('Unresolved episode cannot be silently skipped: '+str(p))
    return pending


def validate_activation(root,revision):
    root,revision=Path(root),Path(revision);a=load(revision/'applied.json')
    if a['root']!=str(root) or a['registration_sha256']!=sha(root/'registration.json'):
        raise ValueError('Unregistered corrective runtime')
    for p,h in a['files'].items():
        if sha(p)!=h:raise ValueError('Corrective runtime drift: '+p)
    with (revision/'activation.json').open('x') as f:
        json.dump({'pid':os.getpid(),'started':time.time(),'registration_sha256':a['registration_sha256']},f)


def passing_tests(path):
    cases=list(ET.parse(path).getroot().iter('testcase'))
    if len(cases)<30 or any(c.find(t) is not None for c in cases for t in ('failure','error')):
        raise ValueError('Passing corrective tests required')


def stage_runtime(root,revision):
    stages={}
    for base in (root/'code/memory/scripts',root/'nb/code/memory/scripts'):
        for name in (*SCRIPTS,'t2a_batch.py'):
            dest=base/name
            content=integrate_batch(dest.read_text()) if name=='t2a_batch.py' else (Path(__file__).parent/name).read_text()
            compile(content,str(dest),'exec')
            staged=revision/'staged'/dest.relative_to(root);staged.parent.mkdir(parents=True,exist_ok=True);staged.write_text(content)
            stages[str(dest)]={'staged':str(staged),'sha256':sha(staged),
                               'previous_sha256':sha(dest) if dest.exists() else None}
    repo=Path(__file__).resolve().parents[2]
    for code in (root/'code',root/'nb/code'):
        for name in ('run_strict_signoff.sh','meter_checker.py'):
            rel=Path('r2g-skills/signoff-loop/scripts/flow')/name;dest=code/rel
            source=repo/rel;staged=revision/'staged'/dest.relative_to(root)
            staged.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,staged)
            stages[str(dest)]={'staged':str(staged),'sha256':sha(staged),
                               'previous_sha256':sha(dest) if dest.exists() else None}
    return stages


def refresh(root,revision,tests):
    """Update a NEVER-LAUNCHED preparation; retain its old code and receipts.

    Native Make input receipts can be reused only if every task and receipt
    remains byte-identical. No activation or live worker is ever refreshed.
    """
    import t2_freepdk45_main as M
    root,revision=Path(root).resolve(),Path(revision).resolve()
    if (revision/'applied.json').exists() or (revision/'activation.json').exists():
        raise ValueError('Cannot refresh an installed/activated revision')
    if load(revision/'operator.json')['status']!='PREPARED_VERIFIED':
        raise ValueError('Only an unlaunched preparation may be refreshed')
    passing_tests(tests);grant=load(revision/'registration.json')
    if grant['root']!=str(root) or sha(root/'registration.json')!=grant['base_registration_sha256'] or sha(root/'nb/registration.json')!=grant['base_nb_registration_sha256']:
        raise ValueError('Live registration changed')
    M.F.verify_files(load(root/'registration.json')['files'])
    M.F.verify_files(load(root/'nb/registration.json')['files'])
    for file,key in [('offline-tests.xml','offline_tests_sha256'),('input-audit.json','input_audit_sha256'),
                     ('new-baseline-definition.json','baseline_definition_sha256')]:
        if sha(revision/file)!=grant[key]:raise ValueError('Prepared evidence drift')
    M.F.verify_files(load(revision/'new-baseline-definition.json')['new_task_files'])
    for row in load(revision/'input-audit.json')['tasks']:
        if sha(row['receipt'])!=row['sha256']:raise ValueError('Input audit receipt drift')
    for dest,change in grant['files'].items():
        if (sha(dest) if Path(dest).exists() else None)!=change['previous_sha256'] or sha(change['staged'])!=change['sha256']:
            raise ValueError('Prepared runtime drift')
    oldsha=sha(revision/'registration.json');archive=revision/'prior-preparations'/oldsha
    archive.mkdir(parents=True,exist_ok=False)
    for name in ('registration.json','operator.json','offline-tests.xml'):
        shutil.copy2(revision/name,archive/name)
    shutil.copytree(revision/'staged',archive/'staged')
    grant['files']=stage_runtime(root,revision)
    shutil.copy2(tests,revision/'offline-tests.xml')
    grant.update(operator_sha256=sha(__file__),offline_tests_sha256=sha(revision/'offline-tests.xml'),
                 previous_preparation_sha256=oldsha,refreshed=time.time(),
                 arm_review_scope='R2G,RET,RECIPE,EXPEL,AWM,AMEM,ADAMEM,REASONINGBANK,TM0; offline tests, not new real-run validation')
    save(revision/'registration.json',grant)


def prepare(root,revision,tests):
    import t2_freepdk45_main as M
    root,revision=Path(root).resolve(),Path(revision).resolve()
    if revision.exists():raise FileExistsError('No overwrite of a corrective revision')
    passing_tests(tests)
    original=load(root/'registration.json');nbreg=load(root/'nb/registration.json')
    M.F.verify_files(original['files']);M.F.verify_files(nbreg['files'])
    revision.mkdir(parents=True);shutil.copy2(tests,revision/'offline-tests.xml')
    reg=copy.deepcopy(nbreg);registry=load(root/'nb/origin-registry.json');edits=[];new_files={};startups={}
    for task in reg['tasks']:
        old=Path(task['template']);cfg=E.effective_config((old/'constraints/config.mk').read_text())
        normalized=uniform_initial(cfg);hid=task['handoff_id']
        if normalized==cfg:continue
        current=root/'nb/episodes'/(task['task'].replace('/','__')+'__NB__r1')
        if current.exists():
            e=load(current/'episode.json')
            if e.get('error')!='Initial flow safety/accounting stop: 76' or e.get('accounting',{}).get('llm_requests')!=0:
                raise ValueError('Do not redefine a measured/running task: '+hid)
            startups[hid]=str(current)
        new=old.with_name(old.name+'_util20')
        if new.exists():raise FileExistsError('Corrected template already exists')
        shutil.copytree(old,new)
        normalized={k:v.replace(str(old)+'/',str(new)+'/') for k,v in normalized.items()}
        (new/'constraints/config.mk').write_text(''.join('export '+k+' = '+v+'\n' for k,v in normalized.items()))
        man=load(new/'manifest.json');old_task=task['task'];new_task=old_task.rsplit('/',1)[0]+'/initial_util20'
        man.update(task=new_task,source_project=str(new),initial_config=normalized,
                   initial_config_sha256=sha(new/'constraints/config.mk'))
        save(new/'manifest.json',man)
        task.update(task=new_task,template=str(new));registry[hid]['task']=new_task
        reg['plan']=[{**p,'task':new_task} if p['task']==old_task else p for p in reg['plan']]
        new_files.update(M.F.frozen(new))
        edits.append({'design':hid,'old_task':old_task,'new_task':new_task,'old_template':str(old),
            'new_template':str(new),'only_scientific_knobs_removed':{k:cfg[k] for k in ('CORE_AREA','DIE_AREA') if k in cfg},
            'clock_sha256_unchanged':sha(old/'constraints/constraint.sdc')==sha(new/'constraints/constraint.sdc'),
            'startup_episode_retained':str(current) if current.exists() else None})
    audit=[]
    flow=Path('/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean/flow')
    resolver=root/'nb/code/r2g-skills/signoff-loop/scripts/flow/t2a_effective_config.py'
    for task in reg['tasks']:
        project=Path(task['template']);man=load(project/'manifest.json');cfg=E.effective_config((project/'constraints/config.mk').read_text())
        check=E.check_config(man,cfg,env={},project=project)
        if not check['ok']:raise ValueError('Static input contract failed: '+task['task']+': '+str(check['violations']))
        out=revision/'input-audit'/task['handoff_id'];out.mkdir(parents=True)
        env=dict(os.environ)
        for k in set(man['initial_config'])|set(man['allowlist'])|set(man['immutable']['knobs']):env.pop(k,None)
        env.update(R2G_TASK_MANIFEST=str(project/'manifest.json'),PYTHONDONTWRITEBYTECODE='1')
        cmd=[sys.executable,str(resolver),'--project',str(project),'--backend',str(out),'--flow',str(flow),
             '--design-config',str(project/'constraints/config.mk'),'--variant',project.name]
        p=subprocess.run(cmd,env=env,cwd=flow,capture_output=True,text=True,timeout=90)
        if p.returncode:raise ValueError('Native Make input audit failed: '+task['task']+' '+p.stderr[-500:])
        receipt=out/'effective-config.json'
        audit.append({'design':task['handoff_id'],'status':load(receipt)['status'],'receipt':str(receipt),'sha256':sha(receipt)})
    save(revision/'input-audit.json',{'status':'PASS','tasks':audit,'EDA_flows':0,'model_requests':0})
    stages=stage_runtime(root,revision)
    save(revision/'new-baseline-definition.json',{'registration':reg,'registry':registry,
         'new_task_files':new_files,'task_definition_corrections':edits,'technical_startup_episodes':startups})
    grant={'schema':VERSION,'root':str(root),'created':time.time(),
        'user_text':'你再修复下，然后仔细检查下看看还有哪里可能会出现问题，一起优化下，然后继续正式实验',
        'base_registration_sha256':sha(root/'registration.json'),'base_nb_registration_sha256':sha(root/'nb/registration.json'),
        'operator_sha256':sha(__file__),'files':stages,'offline_tests_sha256':sha(revision/'offline-tests.xml'),
        'input_audit_sha256':sha(revision/'input-audit.json'),
        'baseline_definition_sha256':sha(revision/'new-baseline-definition.json'),
        'scientific_boundaries':{'tasks':50,'arms':9,'clock_ns':5,'initial_utilization_percent':20,
            'online_requests_per_episode':3,'area_caps':[.55,1.,1.],'cumulative_cost_cap':None,
            'native_full_DRC_deck_unchanged':True,'existing_outcomes_not_repeated':True},
        'technical_timeout_policy':'end one unmeasured cell, retain all 450 denominators and cost; no positive/negative repair evidence',
        'unexpected_errors_accounting_and_UNKNOWN_provider_usage':'pause queue, no implicit retry',
        'history_policy':'identically exclude non-measured technical outcomes from ALL memory constructions; retain measured failures',
        'original_raw_records_unchanged':True,'prepare_wall_safety':'no EDA or model calls'}
    save(revision/'registration.json',grant)
    save(revision/'operator.json',{'status':'PREPARED_VERIFIED','tasks_audited':len(audit),'task_definitions_corrected':len(edits)})


def operate(root,revision):
    import t2_freepdk45_main as M
    import t2_signoff_integrity as S
    root,revision=Path(root).resolve(),Path(revision).resolve();grant=load(revision/'registration.json')
    if grant['root']!=str(root) or grant['operator_sha256']!=sha(__file__):raise ValueError('Operator identity drift')
    lease=(root/'supervisor.lock').open('a')
    while True:
        running=[str(p) for p in (root/'nb/episodes').glob('*/episode.json') if load(p).get('status')=='RUNNING']
        try:fcntl.flock(lease,fcntl.LOCK_EX|fcntl.LOCK_NB);locked=True
        except BlockingIOError:locked=False
        if locked and not running:break
        if locked:fcntl.flock(lease,fcntl.LOCK_UN)
        save(revision/'operator.json',{'status':'WAITING_FOR_STARTED_WORKERS','running':running,'updated':time.time()})
        time.sleep(15)
    try:
        reg=load(root/'registration.json');nbreg=load(root/'nb/registration.json')
        if sha(root/'registration.json')!=grant['base_registration_sha256'] or sha(root/'nb/registration.json')!=grant['base_nb_registration_sha256']:
            raise ValueError('Base changed before corrective installation')
        M.F.verify_files(reg['files']);M.F.verify_files(nbreg['files'])
        for file,key in [('offline-tests.xml','offline_tests_sha256'),('input-audit.json','input_audit_sha256'),
                         ('new-baseline-definition.json','baseline_definition_sha256')]:
            if sha(revision/file)!=grant[key]:raise ValueError('Corrective evidence drift')
        definition=load(revision/'new-baseline-definition.json');M.F.verify_files(definition['new_task_files'])
        for rel in ('registration.json','nb/registration.json','nb/origin-registry.json','dispatch-state.json','nb/dispatch-state.json'):
            dest=revision/'original'/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(root/rel,dest)
        reviews={}
        for task in definition['registration']['tasks']:
            p=root/'nb/episodes'/(task['task'].replace('/','__')+'__NB__r1')
            if not (p/'episode.json').exists():continue
            e=S.effective_episode(p)
            if e['status'] in ('ACCEPTED','NOT_ACCEPTED'):continue
            verdict=e.get('reviewed_verdict') or {};technical=verdict.get('technical_signoff') or {}
            drc=p/'reports/drc.json';d=load(drc) if drc.exists() else {}
            checks=(verdict.get('acceptance') or {}).get('checks') or {}
            if (not technical or d.get('exit_code')!=124 or d.get('run_tag')!=verdict.get('judged_run')
                    or checks.get('A11_provenance')!='pass' or not e.get('accounting',{}).get('passed')):
                raise ValueError('Unrelated unresolved technical cell blocks activation: '+str(p))
            report=revision/'outcome-reviews'/(p.name+'.json')
            report.parent.mkdir(parents=True,exist_ok=True)
            save(report,{'classification':'UNVERIFIED_OR_SAFETY_STOP','raw_status':load(p/'episode.json')['status'],
                'episode_sha256':sha(p/'episode.json'),'reports':{v['report']:v['report_sha256'] for v in technical['issues']},
                'reason':'verified checker timeout, including the legacy aggregate-deadline side effects',
                'not_a_measured_repair_failure':True,'history_admission':False})
            reviews[str(p)]={'path':str(report),'sha256':sha(report)}
        for dest,change in grant['files'].items():
            path=Path(dest)
            if (sha(path) if path.exists() else None)!=change['previous_sha256'] or sha(change['staged'])!=change['sha256']:
                raise ValueError('Runtime delta drift: '+dest)
        for dest,change in grant['files'].items():
            path=Path(dest)
            if path.exists():
                old=revision/'original'/path.relative_to(root);old.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,old)
            shutil.copy2(change['staged'],path)
        changes={p:d['sha256'] for p,d in grant['files'].items()}
        save(root/'nb/origin-registry.json',definition['registry'])
        updated=definition['registration'];updated['acknowledged_technical_stops']=reviews
        updated['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'nb/code')})
        updated['files'].update(definition['new_task_files'])
        updated['files'][str(root/'nb/origin-registry.json')]=sha(root/'nb/origin-registry.json')
        updated['files'].update({v['path']:v['sha256'] for v in reviews.values()})
        updated['shared_corrective_revision']={'path':str(revision),'sha256':sha(revision/'registration.json')}
        save(root/'nb/registration.json',updated)
        reg['files'].update({p:h for p,h in changes.items() if Path(p).is_relative_to(root/'code')})
        reg['files'].update(definition['new_task_files']);reg['files'][str(root/'nb/registration.json')]=sha(root/'nb/registration.json')
        reg['technical_startup_episodes']=definition['technical_startup_episodes'];reg['shared_corrective_revision']=updated['shared_corrective_revision']
        save(root/'registration.json',reg)
        M.F.verify_files(reg['files']);M.F.verify_files(updated['files'])
        save(revision/'applied.json',{'root':str(root),'applied':time.time(),'registration_sha256':sha(root/'registration.json'),
             'files':changes,'known_unmeasured_reviews':reviews,'raw_episodes_preserved':True})
        save(revision/'operator.json',{'status':'INSTALLED_VERIFIED_ACTIVATING','updated':time.time()})
    finally:fcntl.flock(lease,fcntl.LOCK_UN);lease.close()
    os.execv(sys.executable,[sys.executable,str(root/'code/memory/scripts/t2_freepdk45_main.py'),
        'run','--root',str(root),'--correction',str(revision)])


def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('prepare','refresh','operate'))
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--revision',type=Path,required=True)
    ap.add_argument('--tests',type=Path);a=ap.parse_args()
    if a.command=='prepare':prepare(a.root,a.revision,a.tests)
    elif a.command=='refresh':refresh(a.root,a.revision,a.tests)
    else:operate(a.root,a.revision)


if __name__=='__main__':main()
