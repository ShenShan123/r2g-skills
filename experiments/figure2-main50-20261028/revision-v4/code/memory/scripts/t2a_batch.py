#!/usr/bin/env python3
"""Guarded seven-arm Table 2 package and episode worker.

prepare never runs EDA or a model. It freezes current inputs, including incomplete
gates. A blocked package cannot be patched into a launchable one: prepare a new
package after qualification, construction and real DEV validation are complete.
"""
import argparse
import fcntl
import hashlib
import itertools
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import t2a_adapters as A
import t2a_envelope as E
import t2a_qualify as Q
import t2a_runtime as T
import t2_agent_verdict as AV
import t2a_mechanisms as M

PROMPT_ARMS=('RET','EXPEL','AWM','AMEM')
ORDER_SEED='t2a-20261011'
CPU_SLOTS=[list(range(5,13))+list(range(101,109)),list(range(27,35))+list(range(123,131))]
M0_SHA='69f3eebc36a90d6967b2bc62fddd732db1a19f686213ff67758d4af1527355ef'
MECHANISM_DEV_TASKS={'chacha_core/u72':('secworks','sky130hd','72'),
                     'picorv32/u89':('YosysHQ/picorv32','sky130hd','89'),
                     'riscv_core/u19':('ultraembedded/riscv','sky130hs','19')}


def validate_cpu_slots(slots,available,split='TEST'):
    if split=='TEST' and len(slots)!=2:
        raise RuntimeError('Formal scheduling remains two-slot')
    if not slots or len(slots)>7:raise RuntimeError('Invalid concurrency')
    used=set()
    for slot in slots:
        if not slot or len(set(slot))!=len(slot) or used&set(slot):
            raise RuntimeError('CPU sets overlap or contain duplicates')
        if not set(slot)<=set(available):raise RuntimeError('Frozen CPUs unavailable')
        used.update(slot)


def plan_waves(plan,width):
    if width<=0:raise ValueError('Invalid wave width')
    waves=[]
    for _,group in itertools.groupby(plan,key=lambda r:(r['task'],r['repeat'])):
        group=list(group)
        waves.extend(group[i:i+width] for i in range(0,len(group),width))
    return waves


DELTA_BOUNDARY='REAL_SEVEN_ARM_DEV_ON_BASE_PLUS_TEST_AND_REPLAY_VERIFIED_DELTA'


def runtime_delta_errors(dev_hashes,runtime_hashes,delta):
    """TEST runtime identity (2026-10-16). Without a delta the runtime must equal the DEV-validated one.
    With an explicit delta it may differ ONLY by the registered per-file changes from the named base,
    each with its base and new hash, plus passing test and replay evidence. Anything unregistered is
    refused. The boundary is stated, never upgraded to a real seven-arm DEV of the new runtime."""
    dev_hashes=dev_hashes or {}
    if delta is None:
        return [] if dev_hashes==runtime_hashes else ['DEV did not validate this exact runtime']
    errors=[]
    if delta.get('schema')!='t2a-runtime-delta-v1':errors.append('delta schema missing or unknown')
    if delta.get('validation_boundary')!=DELTA_BOUNDARY:
        errors.append('delta validation boundary must be '+DELTA_BOUNDARY)
    if delta.get('base_runtime_hashes')!=dev_hashes:errors.append('delta base is not the DEV-validated runtime')
    ev=delta.get('evidence') or {}
    if not (isinstance(ev.get('junit_sha256'),str) and len(ev['junit_sha256'])==64
            and isinstance(ev.get('passed'),int) and ev['passed']>0 and ev.get('failed')==0
            and isinstance(ev.get('replay'),str) and ev['replay']):
        errors.append('delta evidence incomplete or failing (junit sha256, passed>0, failed==0, replay)')
    if not isinstance(delta.get('disclosures'),list):errors.append('delta disclosures missing')
    files=delta.get('files') if isinstance(delta.get('files'),dict) else {}
    for rel,entry in sorted(files.items()):
        if (entry or {}).get('base')!=dev_hashes.get(rel):errors.append('registered base hash mismatch: '+rel)
        if (entry or {}).get('new')!=runtime_hashes.get(rel):errors.append('registered new hash mismatch: '+rel)
    for rel in sorted((set(dev_hashes)|set(runtime_hashes))-set(files)):
        if dev_hashes.get(rel)!=runtime_hashes.get(rel):errors.append('unregistered runtime difference: '+rel)
    return errors


def formal_phase_budget():
    """Table 2's own ledger (user decision 2026-10-16): no cumulative request, token or money cap; usage is
    still metered and an UNKNOWN amount pauses the queue. The historical formal balance stays on record."""
    return {'budget_id':'t2a-table2-independent','caps':{},'reserve':{},'halt_on_unknown_usage':True}


def episode_budget():
    """S1: equal repair opportunities, actual cost as an outcome, not a task cap.

    Phase payment admission and per-invocation watchdogs remain separate. Historical
    frozen packages retain their original budgets; create a new validated package.
    """
    return {'budget_id':'t2a-episode','caps':{'repair_attempts':3},'reserve':{}}


def store_errors(store):
    try:
        receipt=json.loads((store/'m0_receipt.json').read_text())
        if (Q.digest(store/'tehm.sqlite')!=M0_SHA or receipt.get('source_export_sha256')!=A.SOURCE_SHA
            or receipt.get('captured')!=1933 or receipt.get('d1') is not True):
            return ['TEHM-M0-v2/SOURCE identity mismatch']
    except (OSError,ValueError):return ['TEHM-M0-v2 identity unavailable']
    return []


def validate_gates(inventory,controls,dev):
    errors=[]
    if inventory.get('status')!='FROZEN' or inventory.get('source_isolation',{}).get('ok') is not True:
        errors.append('qualified TEST inventory/source isolation not frozen')
    tasks=inventory.get('tasks',[])
    if not tasks or len(tasks)>6 or len({r['task'] for r in tasks})!=len(tasks):
        errors.append('task inventory empty, duplicated or over six')
    for row in tasks:
        if row.get('status')!='ADMITTED':errors.append('task not admitted: '+row['task'])
    for arm in ('RET','RECIPE','EXPEL','AWM','AMEM'):
        p=Path(controls)/(arm+'.json')
        try:
            data=json.loads(p.read_text())
            if data.get('status')!='FROZEN' or data.get('source_sha256')!=A.SOURCE_SHA:
                raise ValueError('Wrong SOURCE or incomplete representation')
        except (OSError,ValueError) as exc:errors.append(arm+': '+str(exc))
    if not dev.get('real_seven_arm_dev_validated') or dev.get('validation_kind')!='REAL_PROVIDER_AND_EDA':
        errors.append('real seven-arm DEV validation pending')
    if dev.get('online_policy')!=T.online_policy():
        errors.append('DEV did not validate the common O1 online policy')
    rows=dev.get('records',[])
    if {r.get('arm') for r in rows}!=set(T.ARMS) or len(rows)!=7 or not all(
          r.get('accounting',{}).get('passed') and r.get('criteria_passed') is True for r in rows):
        errors.append('seven-arm DEV accounting/criteria incomplete')
    return errors


def validate_dev_inventory(inventory):
    """Fixed exposed DEV task only; this path cannot admit TEST candidates."""
    rows=inventory.get('tasks',[])
    if inventory.get('schema')=='t2a-mechanism-dev-inventory-v1':
        if (inventory.get('status')!='FROZEN_DEV' or inventory.get('split')!='DEV'
                or inventory.get('source_sha256')!=A.SOURCE_SHA or len(rows)!=3
                or {r.get('task') for r in rows}!=set(MECHANISM_DEV_TASKS)
                or any(r.get('family')!=MECHANISM_DEV_TASKS[r['task']][0] or r.get('status')!='ADMITTED_DEV' for r in rows)):
            return ['Mechanism DEV cohort must be exactly the three old calibration tasks']
        return []
    if (inventory.get('status')!='FROZEN_DEV' or inventory.get('split')!='DEV'
        or inventory.get('source_sha256')!=A.SOURCE_SHA or len(rows)!=1
        or rows[0].get('task')!='riscv_core/u19' or rows[0].get('family')!='ultraembedded/riscv'
        or rows[0].get('status')!='ADMITTED_DEV'):
        return ['DEV inventory is not the registered exposed riscv_core/u19 task']
    return []


def construction_dev_binding():
    phase=Q.EVAL/'t2a-control-construction-c2-20261011'
    budget=phase/'C8-budget.json';ledger=phase/'phase/resource_ledger.jsonl'
    data=json.loads(budget.read_text())
    if data!={'budget_id':'t2a-construction-dev-A3','caps':{'llm_requests':5000},
              'combined_tokens_cap':17000000,'reserve':{}}:
        raise ValueError('Shared construction + DEV budget changed')
    # One immutable byte prefix: stat followed by a separate digest can race
    # another authorized DEV request appending to this shared live ledger.
    prefix=ledger.read_bytes()
    return {'budget_file':str(budget),'budget_sha256':Q.digest(budget),
            'ledger_path':str(ledger),'prior_ledger_bytes':len(prefix),
            'prior_ledger_sha256':hashlib.sha256(prefix).hexdigest()}


def prepare(root,inventory_path,controls,dev_path,store,split='TEST',dev_cpu_slots=None,mechanism_policy=None,dev_arms=None,delta_path=None):
    if split not in ('TEST','DEV'):raise ValueError('Unknown split')
    if dev_cpu_slots is not None and split!='DEV':raise ValueError('DEV scheduling override cannot change TEST')
    if dev_arms is not None and (split!='DEV' or tuple(dev_arms)!=('NB','TM0')):
        raise ValueError('Only the explicit paired DEV subset NB/TM0 is supported')
    arms=T.ARMS if dev_arms is None else tuple(dev_arms)
    slots=CPU_SLOTS if dev_cpu_slots is None else dev_cpu_slots
    validate_cpu_slots(slots,os.sched_getaffinity(0),split)
    root.mkdir(parents=True,exist_ok=False)
    start,cpu=time.time(),time.process_time()
    inventory=json.loads(inventory_path.read_text());dev=json.loads(dev_path.read_text()) if dev_path else {}
    if delta_path is not None and split!='TEST':raise ValueError('A runtime delta applies to TEST packages only')
    delta=json.loads(Path(delta_path).read_text()) if delta_path else None
    mechanism=M.load_policy(mechanism_policy) if mechanism_policy else None
    if split=='DEV':
        errors=validate_dev_inventory(inventory)+store_errors(store)
        for arm in ('RET','RECIPE','EXPEL','AWM','AMEM'):
            data=json.loads((controls/(arm+'.json')).read_text())
            if data.get('status')!='FROZEN' or data.get('source_sha256')!=A.SOURCE_SHA:
                errors.append('Incomplete DEV memory: '+arm)
        phase_binding=construction_dev_binding()
    else:
        errors=validate_gates(inventory,controls,dev)+store_errors(store)
        phase_binding=None
    for rel in ('r2g-skills/signoff-loop','r2g-skills/def-graph','memory'):
        shutil.copytree(Q.REPO/rel,root/'code'/rel,ignore=shutil.ignore_patterns(
            '__pycache__','*.pyc','.pytest_cache','docs','tests','evaluation'))
    pin=Path('r2g-skills/signoff-loop/references/env.local.sh')
    shutil.copy2(Q.EVAL/'toolchain/frozen-r2g-cc6d103'/pin,root/'code'/pin)
    shutil.copytree(store,root/'store')
    (root/'controls').mkdir()
    for arm in ('RET','RECIPE','EXPEL','AWM','AMEM'):
        p=controls/(arm+'.json')
        if p.exists():shutil.copy2(p,root/'controls'/p.name)
    shutil.copy2(inventory_path,root/'inventory.json')
    if mechanism_policy:shutil.copy2(mechanism_policy,root/'mechanism-policy.json')
    if delta_path:shutil.copy2(delta_path,root/'runtime-delta.json')
    if dev_path:shutil.copy2(dev_path,root/'dev.json')
    else:Q.save(root/'dev.json',{'status':'NOT_EXECUTED','split':'DEV','paper_data':False})
    authorization=Q.EVAL/'t2a-online-input-o1-20261012/authorization.json'
    grant=json.loads(authorization.read_text())
    if (grant.get('schema')!='t2a-online-input-authorization-v1' or grant.get('user_approved') is not True
        or grant.get('online_policy')!=T.online_policy() or grant.get('formal_requests')!=336
        or grant.get('formal_combined_tokens')!=4704000
        or grant.get('construction_dev_requests')!=5000 or grant.get('construction_dev_combined_tokens')!=17000000):
        raise ValueError('Common online input authorization missing or changed')
    shutil.copy2(authorization,root/'online-input-authorization.json')
    tasks=[]
    for row in inventory.get('tasks',[]):
        p=Path(row['manifest'])
        if Q.digest(p)!=row['manifest_sha256']:raise ValueError('Inventory envelope digest mismatch')
        man=json.loads(p.read_text());task=root/'tasks'/row['task'].replace('/','__')
        project=Path(man['source_project'])
        shutil.copytree(project/'constraints',task/'constraints');shutil.copytree(project/'rtl',task/'rtl')
        # Rebase only this task's own files; every external include/hook stays hash-bound.
        cfg=task/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(project)+'/',str(task)+'/'))
        old=man['initial_config'];man['initial_config']=E.effective_config(cfg.read_text())
        if man['initial_config']!={k:v.replace(str(project)+'/',str(task)+'/') for k,v in old.items()}:
            raise ValueError('Task rebase changed more than owned paths')
        man['source_project']=str(task);man['initial_config_sha256']=Q.digest(cfg)
        Q.save(task/'manifest.json',man);tasks.append({'task':row['task'],'template':str(task)})
    Q.save(root/'episode-budget.json',episode_budget())
    if phase_binding:shutil.copy2(phase_binding['budget_file'],root/'phase-budget.json')
    else:Q.save(root/'phase-budget.json',formal_phase_budget())
    plan=[]
    repeats=(1,) if split=='DEV' else (1,2)
    for rep in repeats:
        for t in sorted(tasks,key=lambda row:A.fingerprint([ORDER_SEED,rep,row['task']])):
            order=sorted(arms,key=lambda arm:A.fingerprint([ORDER_SEED,rep,t['task'],arm]))
            plan += [{'task':t['task'],'arm':arm,'repeat':rep} for arm in order]
    frozen=[p for base in ('code','store','controls','tasks') for p in (root/base).rglob('*') if p.is_file()]
    frozen+=[root/n for n in ('inventory.json','dev.json','episode-budget.json','phase-budget.json',
                             'online-input-authorization.json')]
    if mechanism_policy:frozen.append(root/'mechanism-policy.json')
    if delta_path:frozen.append(root/'runtime-delta.json')
    # Also check immutable original hook/include content, not just a copied manifest.
    for t in tasks:
        man=json.loads((Path(t['template'])/'manifest.json').read_text())
        frozen += [Path(p) for p in man['immutable']['files'] if Path(p).is_absolute()]
    files={str(p):Q.digest(p) for p in frozen}
    # Native binaries (plus Tcl launcher payloads) and actual ORFS configuration
    # inputs cannot drift unnoticed while the source code stays at the same hash.
    toolbase=Path('/data1/zhangdy/Tools/tehm-toolchain')
    native=[toolbase/p for p in ('openroad-matched/bin/openroad','yosys/bin/yosys',
        'yosys/share/yosys/plugins/slang.so','sta/bin/sta','klayout/bin/klayout.bin',
        'magic/bin/magic','netgen/bin/netgen')]
    for launcher in ('magic','netgen'):
        native.extend(p for p in (toolbase/launcher/'lib').rglob('*') if p.is_file())
    orfs=Path('/data1/zhangdy/Tools/OpenROAD-flow-scripts-clean/flow')
    native.extend(p for sub in ('scripts','util','platforms/sky130hd','platforms/sky130hs') for p in (orfs/sub).rglob('*') if p.is_file())
    native.append(orfs/'Makefile')
    native.extend(p for p in (toolbase/'pdks/sky130A/libs.tech/netgen').rglob('*') if p.is_file())
    native.extend(p for p in (toolbase/'pdks/sky130A/libs.tech/magic').rglob('*') if p.is_file())
    external_hashes={str(p):Q.digest(p) for p in native}
    files.update(external_hashes)
    if split=='TEST' and dev.get('external_hashes')!=external_hashes:errors.append('DEV native tools/ORFS/PDK identity not bound')
    runtime_hashes={str(p.relative_to(root/'code')):h for p,h in ((Path(k),v) for k,v in files.items())
                    if p.is_relative_to(root/'code')}
    if split=='TEST':errors+=runtime_delta_errors(dev.get('runtime_hashes'),runtime_hashes,delta)
    control_hashes={p.name:Q.digest(p) for p in (root/'controls').glob('*.json')}
    if split=='TEST' and dev.get('control_hashes')!=control_hashes:errors.append('DEV did not validate these exact memories')
    if split=='TEST' and dev.get('mechanism_policy')!=mechanism:errors.append('DEV mechanism policy differs')
    registration={'schema':'t2a-seven-arm-package-v1','registered_at':time.time(),
          'status':'BLOCKED_PREPARATION' if errors else ('FROZEN_DEV_READY' if split=='DEV' else 'FROZEN_READY'),
          'split':split,'paper_data':split=='TEST','launch_blockers':errors,'phase_binding':phase_binding,
          'source_sha256':A.SOURCE_SHA,'tasks':tasks,'plan':plan,'files':files,'repeats':len(repeats),'arms':list(arms),
          'runtime_hashes':runtime_hashes,'external_hashes':external_hashes,'control_hashes':control_hashes,
          'scheduler':{'order_seed':ORDER_SEED,'cpu_slots':slots,'threads_per_episode':8,
                       'load_log_interval_s':30,'pair_barrier':len(slots)==2,'concurrency':len(slots)},
          'preparation_wall_s':time.time()-start,'preparation_cpu_s':time.process_time()-cpu,
          'stopping_rule':'agent_selected_with_three_total_online_requests',
          'per_episode_cost_caps':None,
          'online_policy':T.online_policy(),
          'mechanism_policy':mechanism,
          'dev_validation_boundary':(None if split!='TEST' else DELTA_BOUNDARY if delta else 'REAL_SEVEN_ARM_DEV_EXACT_RUNTIME'),
          'runtime_delta':({'files':sorted(delta.get('files',{})),'disclosures':delta.get('disclosures'),
                            'base_registration_sha256':delta.get('base_registration_sha256')} if delta else None),
          'tehm_evidence_policy':{'caliber':'main','require_verified_positive':True,
                                  'witness_units':'canonical_own_physical_attempts'},
          'new_eda_flows':0,'new_llm_requests':0,'no_implicit_retry':True}
    Q.save(root/'registration.json',registration)
    print(json.dumps({'root':str(root),'status':registration['status'],'episodes_planned':len(plan),'blockers':errors}))


def preflight(root,split='TEST'):
    reg=json.loads((root/'registration.json').read_text())
    errors=list(reg.get('launch_blockers',[]))
    expected='FROZEN_READY' if split=='TEST' else 'FROZEN_DEV_READY'
    if reg.get('status')!=expected or reg.get('split','TEST')!=split:errors.append('Package is not launchable for '+split)
    if reg.get('online_policy')!=T.online_policy():errors.append('Frozen online policy differs')
    mechanism_path=root/'mechanism-policy.json'
    if mechanism_path.exists():
        if M.load_policy(mechanism_path)!=reg.get('mechanism_policy'):errors.append('Frozen mechanism policy differs')
    elif reg.get('mechanism_policy') is not None:errors.append('Frozen mechanism policy missing')
    for p,h in reg['files'].items():
        if not Path(p).is_file() or Q.digest(p)!=h:errors.append('Frozen input changed: '+p)
    if split=='DEV':
        binding=reg.get('phase_binding')
        if not binding:errors.append('Shared construction + DEV ledger binding missing')
        else:
            if Q.digest(binding['budget_file'])!=binding['budget_sha256']:errors.append('Shared DEV budget changed')
            prefix=Path(binding['ledger_path']).read_bytes()[:binding['prior_ledger_bytes']]
            if hashlib.sha256(prefix).hexdigest()!=binding['prior_ledger_sha256']:
                errors.append('Prior shared DEV ledger changed')
    if errors:raise RuntimeError('\n'.join(errors))
    return reg


def deadline_timeout(H,project,purpose='repair'):
    rc,raw=H._ledger(project,'remaining','--purpose',purpose)
    if rc:raise RuntimeError('Accounting error reading deadline')
    left=json.loads(raw).get('wall_s')
    if left=='UNKNOWN':raise RuntimeError('Attempt time UNKNOWN')
    return 28800 if left is None else max(1,left-H.KILL_GRACE_S)


def mark_engineer_restore(project,fix_row_offset=0):
    fixlog=project/'reports/fix_log.jsonl'
    rows=[json.loads(l) for l in fixlog.read_text().splitlines() if l] if fixlog.exists() else []
    rows=rows[fix_row_offset:]
    # stop_clean/stop_residual rows do not execute a flow and must not conceal
    # the rollback of the last actual execution. A later kept flow supersedes it.
    last_execution=next((r for r in reversed(rows) if r.get('from_stage')),None)
    reason=None
    if last_execution and last_execution.get('rolled_back'):
        reason='shared repair loop rolled back its last execution'
    pointer=project/'backend/.r2g_signoff_run'
    manifest=project/'manifest.json'
    if pointer.exists() and manifest.exists() and json.loads(manifest.read_text()).get('require_execution_binding'):
        try:
            run=Path(json.loads(pointer.read_text())['run_dir'])
            if not run.resolve().is_relative_to((project/'backend').resolve()):
                raise ValueError('Active pointer outside episode')
            binding=json.loads((run/'effective-config.json').read_text())
            if binding.get('status')!='PASS' or binding.get('project_config_sha256')!=Q.digest(project/'constraints/config.mk'):
                reason='active configuration is not bound to the active execution'
        except (OSError,ValueError,KeyError):
            reason='active execution binding unavailable'
    if manifest.exists():
        for rel,want in (json.loads(manifest.read_text()).get('immutable') or {}).get('files',{}).items():
            path=project/rel
            if not path.is_file() or 'sha256:'+Q.digest(path)!=want:
                reason='immutable task input changed: '+rel
    if reason:
        (project/'reports/t2a-workspace-unsafe.json').write_text(A.stable({
            'reason':reason,'source':'shared_repair_boundary',
            'baseline_partial':T.partial_execution_proof(project)})+'\n')


def episode(root,task,arm,repeat,split='TEST',resume_initial=False):
    setup_started,worker_cpu=time.time(),time.process_time()
    reg=preflight(root,split)
    if Path(__file__).resolve()!=Path(reg.get('shared_worker_file',root/'code/memory/scripts/t2a_batch.py')):
        raise RuntimeError('Worker must execute the hash-bound private runtime, not the worktree')
    if {'task':task,'arm':arm,'repeat':repeat} not in reg['plan']:raise ValueError('Unregistered episode')
    template=Path(next(t['template'] for t in reg['tasks'] if t['task']==task))
    name=task.replace('/','__')+'__'+reg.get('figure2_variant',arm)+'__r'+str(repeat)
    project=root/'episodes'/name
    prior_record=None
    if resume_initial:
        from t2_freepdk45_recovery import check_initial_checkpoint
        prior_record=check_initial_checkpoint(project)
    else:project.mkdir(parents=True,exist_ok=False)
    lock=(project/'worker.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if not resume_initial:
        # Fresh initial flow, never a copied qualification backend or workspace.
        shutil.copytree(template/'constraints',project/'constraints');shutil.copytree(template/'rtl',project/'rtl')
        for sub in ('backend','reports','input','drc','lvs','rcx','lint','synth','sim'):(project/sub).mkdir()
        cfg=project/'constraints/config.mk';cfg.write_text(cfg.read_text().replace(str(template)+'/',str(project)+'/'))
        man=json.loads((template/'manifest.json').read_text());man['initial_config']=E.effective_config(cfg.read_text())
        man['initial_config_sha256']=Q.digest(cfg);man['source_project']=str(project);Q.save(project/'manifest.json',man)
    else:
        cfg=project/'constraints/config.mk';man=json.loads((project/'manifest.json').read_text())
    H=Q.module('t2a_formal_harness',root/'code/memory/scripts/r2g_memory_eval.py')
    T.configure_online(H)
    H.SKILL=root/'code/r2g-skills/signoff-loop';H.FLOW=H.SKILL/'scripts/flow';H.LOOP=H.SKILL/'scripts/loop/engineer_loop.py'
    H.EVAL=root;H.CASES=root/'tasks';H.CFG={'platform':man['platform']};H._EPISODE['ledger']=project
    # One process per episode. Clear inherited campaign knobs, never source credentials into a log.
    for key in ('R2G_LEDGER_PATH','R2G_BUDGET_PURPOSE','R2G_CHECKER_DEADLINE','FROM_STAGE','R2G_TASK_MANIFEST',
                'R2G_RECIPE_FILE','R2G_RECIPE_SUSPENDED','TEHM_DB','TEHM_ARTIFACTS_ROOT',
                'R2G_TEHM_PRIORITY_FILE','R2G_ENGINEER_BRANCH','R2G_TEHM_V2_POLICY'):
        os.environ.pop(key,None)
    binding=reg.get('phase_binding') or {}
    os.environ.update(R2G_BUDGET_FILE=str(root/'episode-budget.json'),R2G_REQUIRE_LLM_BUDGET='1',
            R2G_REQUIRE_TASK_MANIFEST='1',R2G_PHASE_LEDGER=binding.get('ledger_path',str(root/'phase/resource_ledger.jsonl')),
            R2G_PHASE_BUDGET_FILE=binding.get('budget_file',str(root/'phase-budget.json')))
    env=H.base_env('tehm' if arm=='TM0' else 'none')
    env.update(TEHM_EVIDENCE_CALIBER='main',TEHM_D1_REQUIRE_VERIFIED_POSITIVE='1',
               R2G_METER_STRICT_CHECKERS='1',LVS_CRASH_RETRIES='1')
    mechanism=reg.get('mechanism_policy') or {}
    if arm=='TM0' and mechanism.get('cost_priority'):
        env['R2G_TEHM_PRIORITY_FILE']=str(root/'mechanism-policy.json')
    # TEHM-v2 (2026-10-18): a package that registers a policy file gives it to the TEHM arm only.
    if arm=='TM0' and (root/'tehm-v2-policy.json').is_file():
        env['R2G_TEHM_V2_POLICY']=str(root/'tehm-v2-policy.json')
    env.update(R2G_TASK_MANIFEST=str(project/'manifest.json'),R2G_ENV_FILE=str(H.SKILL/'references/env.local.sh'),
            R2G_TEHM_ROOT=str(root/'code/memory'),R2G_JOURNAL_DB=str(project/'journal.sqlite'),
            PYTHONPATH=str(root/'code/memory')+':'+str(root/'code/memory/scripts'),
            PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='',R2G_BUDGET_COMPONENT='initial',
            R2G_VERIFICATION_PURPOSE='repair')
    start=time.time();record={'task':task,'arm':arm,'repeat':repeat,'split':split,'paper_data':reg.get('paper_data',split=='TEST'),
            'status':'RUNNING','started':start,
            'setup_wall_s':start-setup_started,'setup_cpu_s':time.process_time()-worker_cpu}
    if prior_record:
        record['started']=prior_record['started']
        record['initial_continuation']={'original_error':prior_record['error'],'checkpoint_reused':True,'initial_flow_repeated':False,'resume_started':start}
    Q.save(project/'episode.json',record)
    T.event(H,project,'attempt_resume' if resume_initial else 'attempt_start',{'task':task,'arm':arm,'repeat':repeat,
              'registration_sha256':Q.digest(root/'registration.json'),'cpu_affinity':sorted(os.sched_getaffinity(0)),
              'primary_opportunity_cap':3,'wall_safety_cap':None,'flow_cap':None,
              'online_policy':T.online_policy(),
              'stopping_rule':'agent_selected_with_three_total_online_requests',
              'per_invocation_supervisor_watchdog_s':28800})
    try:
        if arm=='TM0':
            snapshot_start,snapshot_cpu=time.time(),time.process_time()
            sys.path.insert(0,str(root/'code/memory'))
            from tehm.snapshot import bind_snapshot,read_snapshot
            bind_snapshot(root/'agent-store',project/'.tehm-store')
            env.update(TEHM_DB=str(project/'.tehm-store/tehm.sqlite'),TEHM_ARTIFACTS_ROOT=str(project/'.tehm-store/artifacts'))
            T.event(H,project,'memory_snapshot',{**read_snapshot(project/'.tehm-store'),
                    'wall_s':time.time()-snapshot_start,'cpu_s':time.process_time()-snapshot_cpu})
        if arm=='RECIPE':env['R2G_RECIPE_FILE']=str(root/'controls/RECIPE.json')
        # All initial/preparation work after attempt_start spends wall time.
        local_start,local_cpu=time.time(),time.process_time()
        from t2_agent_memories import make_memory
        memory=make_memory(H,project,arm,root,env)
        T.event(H,project,'memory_runtime_initialization',{'arm':arm,'wall_s':time.time()-local_start,
                      'cpu_s':time.process_time()-local_cpu,'zero_provider_requests':True})
        if not resume_initial:
            rc=H.run(['bash',H.FLOW/'run_orfs.sh',project,H.CFG['platform']],env,project/'eval_arm.log',deadline_timeout(H,project))
        else:
            rc=2 # original, charged PDN failure; never re-launch its initial flow
        if rc in (75,76,-9,124,137):raise RuntimeError('Initial flow safety/accounting stop: '+str(rc))
        H.run(['python3',H.SKILL/'scripts/extract/extract_ppa.py',project,project/'reports/ppa.json'],env,project/'eval_arm.log',600)
        if man.get('area_reference_policy'):
            from t2_nb_firstlook import resolve_area_reference
            resolve_area_reference(man)
            T.event(H,project,'initial_area_reference',{'status':'BOUND_BEFORE_REPAIR','receipt':'reports/initial-area-reference.json'})
        initial=H.final_verdict(project,env,project/'eval_arm.log')
        record['initial']=initial
        if initial.get('technical_signoff'):
            raise T.TechnicalFailure('Initial signoff was not measured; no model repair of checker failure')
        initial_acc=initial.get('acceptance') or {}
        if ((initial_acc.get('checks') or {}).get('A11_provenance')=='fail'
                or (initial.get('verification_stop') and initial_acc.get('accepted'))):
            raise RuntimeError('Technical signoff/provenance failure before repair')
        if not initial['accepted']:
            from t2_agent_runtime import run as run_agent
            key=H.llm_key('/data1/zhangdy/.llm_env')
            record['agent_run']=run_agent(H,project,arm,env,key,initial,mark_engineer_restore,memory=memory)
            record['protocol_stop_reason']=record['agent_run']['stop_reason']
            record['repair_turns']=[d['execution'] for d in record['agent_run']['decisions'] if 'execution' in d]
        final=AV.terminal_verdict(H,project,env);record['final']=final
        record['status']=AV.terminal_status(final)
    except Exception as exc:
        record.update(status='UNVERIFIED_OR_SAFETY_STOP',error=str(exc))
        if isinstance(exc,T.TechnicalFailure):record['technical_failures']=T.technical_failures(project)
    finally:
        events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines() if l]
        record.update(ended=time.time(),wall_s=time.time()-start,
          total_worker_wall_s=time.time()-setup_started,harness_cpu_s=time.process_time()-worker_cpu,
          token_usage=T.request_token_totals(events),
          accounting=T.accounting_check(events,list((project/'backend').glob('RUN_*'))))
        rc,raw=H._ledger(project,'summary','--json');record['resources']=json.loads(raw) if rc==0 else 'UNKNOWN'
        if record['accounting']['llm_requests']>3:
            record['accounting']['passed']=False
            record['accounting']['errors'].append('three total online requests exceeded')
        if not record['accounting']['passed']:record['status']='ACCOUNTING_FAILURE'
        if prior_record:
            record['total_worker_wall_s']+=prior_record.get('total_worker_wall_s',0)
            record['harness_cpu_s']+=prior_record.get('harness_cpu_s',0)
            record['wall_s']+=prior_record.get('wall_s',0)
            record['paused_wall_s']=max(0,start-prior_record.get('ended',start))
            record['elapsed_including_pause_s']=record['ended']-record['started']
        record['terminal_reason']=('accounting_failure' if record['status']=='ACCOUNTING_FAILURE' else
            'technical_failure' if record.get('technical_failures') else
            'episode_exception' if record.get('error') else AV.terminal_reason(
                record.get('final') or {},record['accounting']['repair_attempts']))
        record['accepted']=record['status']=='ACCEPTED';Q.save(project/'episode.json',record)
        H._EPISODE['ledger']=None
    print(json.dumps({'episode':name,'status':record['status'],'accepted':record['accepted']}))


def batch(root,split='TEST'):
    reg=preflight(root,split)
    if (root/'batch-start.json').exists():raise RuntimeError('No implicit batch restart or retry')
    for row in reg['plan']:
        p=root/'episodes'/(row['task'].replace('/','__')+'__'+row['arm']+'__r'+str(row['repeat']))
        if p.exists():raise RuntimeError('Existing episode; no implicit retry: '+str(p))
    slots=reg['scheduler']['cpu_slots']
    validate_cpu_slots(slots,os.sched_getaffinity(0),split)
    Q.save(root/'batch-start.json',{'started':time.time(),'registration_sha256':Q.digest(root/'registration.json'),
           'episodes':len(reg['plan']),'cpu_slots':slots})
    worker=root/'code/memory/scripts/t2a_batch.py'
    if Q.digest(worker)!=reg['files'][str(worker)]:raise RuntimeError('Worker code changed')
    env=dict(os.environ);env.update(PYTHONDONTWRITEBYTECODE='1',CUDA_VISIBLE_DEVICES='')
    # Barriers keep each registered wave's launch load aligned. A worker never retries an
    # unfinished prior episode. Per-episode exceptions become explicit records.
    with (root/'machine-load.jsonl').open('a') as load:
        # Never pair a task's last arm with a different task's first. TEST keeps
        # two-slot waves; explicitly frozen DEV may validate seven in parallel.
        waves=plan_waves(reg['plan'],len(slots))
        for index,wave in enumerate(waves):
            # Integrity fail-fast (2026-10-16): an operator pause request stops dispatch at a wave boundary.
            # Running episodes are never killed here and nothing is retried; the batch records the pause.
            pause=root/'dispatch-pause.json'
            if pause.exists():
                Q.save(root/'batch-paused.json',{'paused_at':time.time(),'waves_completed':index,
                       'waves_planned':len(waves),'request':json.loads(pause.read_text())})
                return
            children=[];handles=[]
            for slot,row in zip(slots,wave):
                log=root/(row['task'].replace('/','__')+'__'+row['arm']+'__r'+str(row['repeat'])+'.worker.log')
                handle=log.open('a');handles.append(handle)
                cmd=['taskset','-c',','.join(map(str,slot)),sys.executable,str(worker),'episode','--root',str(root),
                     '--task',row['task'],'--arm',row['arm'],'--repeat',str(row['repeat']),'--split',split]
                children.append(subprocess.Popen(cmd,env=env,stdout=handle,stderr=subprocess.STDOUT,start_new_session=True))
            try:
                while any(c.poll() is None for c in children):
                    load.write(A.stable({'ts':time.time(),'loadavg':os.getloadavg(),'slots':slots})+'\n');load.flush()
                    # This is the durable background batch supervisor, not an assistant wait call.
                    time.sleep(reg['scheduler']['load_log_interval_s'])
                if any(c.returncode for c in children):raise RuntimeError('Worker aborted; do not restart implicitly')
            finally:
                for handle in handles:handle.close()
    Q.save(root/'batch-complete.json',{'ended':time.time(),'episodes':len(reg['plan'])})


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('command',choices=('prepare','preflight','episode','batch'))
    ap.add_argument('--root',type=Path,required=True);ap.add_argument('--inventory',type=Path)
    ap.add_argument('--controls',type=Path);ap.add_argument('--dev',type=Path);ap.add_argument('--store',type=Path)
    ap.add_argument('--task');ap.add_argument('--arm',choices=T.ARMS);ap.add_argument('--repeat',type=int,choices=(1,2))
    ap.add_argument('--split',choices=('TEST','DEV'),default='TEST')
    args=ap.parse_args()
    if args.command=='prepare':prepare(args.root.resolve(),args.inventory.resolve(),args.controls.resolve(),args.dev.resolve(),args.store.resolve())
    elif args.command=='preflight':
        reg=preflight(args.root.resolve(),args.split);print(A.stable({'status':reg['status'],'episodes':len(reg['plan'])}))
    elif args.command=='batch':batch(args.root.resolve(),args.split)
    else:episode(args.root.resolve(),args.task,args.arm,args.repeat,args.split)
