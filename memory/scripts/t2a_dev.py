#!/usr/bin/env python3
"""Seven real DEV episodes on one exposed, fixed task; never paper TEST data."""
import argparse
import json
from pathlib import Path
import re
import shutil
import time

import t2a_adapters as A
import t2a_batch as B
import t2a_envelope as E
import t2a_qualify as Q
import t2a_task_manifests as M

TASK='riscv_core/u19'
QUALIFICATION=Q.EVAL/'qualification-hhs/riscv_core__u19.json'
CONTROLS=Q.EVAL/'t2a-control-construction-c2-20261011'
STORE=Q.EVAL/'stores-v1/TEHM-M0-v2'
SEVEN_CPU_SLOTS=[list(range(lo,lo+8))+list(range(lo+96,lo+104))
                 for lo in (5,27,48,56,64,72,80)]


def inputs(output,task=None):
    start,cpu=time.time(),time.process_time()
    qualification=QUALIFICATION
    if task is not None:
        if task not in B.MECHANISM_DEV_TASKS:raise ValueError('Not a registered historical DEV task')
        qualification=Q.EVAL/{'chacha_core/u72':'qualification-fhd/chacha_core__u72.json',
                             'picorv32/u89':'qualification-hhd/picorv32__u89.json',
                             'riscv_core/u19':'qualification-hhs/riscv_core__u19.json'}[task]
    task=task or TASK
    family,platform,util=B.MECHANISM_DEV_TASKS[task]
    raw=json.loads(qualification.read_text());source=Path(raw['project'])
    if (raw.get('design')!=task.split('/')[0] or raw.get('variant')!=task.split('/')[1]
            or (task==TASK and not raw.get('final_layout'))):
        raise ValueError('Fixed exposed DEV qualification unavailable')
    # Fold literal continuations only, then pin the exact same RTL bytes in
    # this new owned task. No legacy variant or completed backend is modified.
    text=re.sub(r'\\\s*\n[ \t]*',' ',(source/'constraints/config.mk').read_text())
    original=E.effective_config(text)
    if original.get('CORE_UTILIZATION')!=util or original.get('PLATFORM')!=platform:
        raise ValueError('DEV initial pressure/platform changed')
    output.mkdir(parents=True,exist_ok=False);project=output/'initial-task'
    (project/'rtl').mkdir(parents=True);(project/'constraints').mkdir()
    names=set();input_hashes={str(qualification):Q.digest(qualification)}
    for item in original['VERILOG_FILES'].split():
        p=Path(item)
        if p.name in names:raise ValueError('Duplicate RTL basename')
        names.add(p.name);input_hashes[str(p)]=Q.digest(p)
        shutil.copy2(p,project/'rtl'/p.name)
        text=text.replace(str(p),str(project/'rtl'/p.name))
    sdc=source/'constraints/constraint.sdc'
    shutil.copy2(sdc,project/'constraints/constraint.sdc')
    input_hashes[str(sdc)]=Q.digest(sdc)
    input_hashes[str(source/'constraints/config.mk')]=Q.digest(source/'constraints/config.mk')
    text=text.replace(original['SDC_FILE'],str(project/'constraints/constraint.sdc'))
    (project/'constraints/config.mk').write_text(text)
    config=E.effective_config(text)
    clock=re.search(r'^\s*set\s+clk_period\s+([0-9.]+)',sdc.read_text(),re.M)
    if not clock:raise ValueError('Unresolved DEV clock')
    reference=sorted((source/'backend').glob('RUN_*'))[0]/'logs/2_1_floorplan.json'
    areas=M.areas(reference,M.FLOOR_KEYS);input_hashes[str(reference)]=Q.digest(reference)
    immutable={'constraints/constraint.sdc':'sha256:'+Q.digest(sdc)}
    immutable.update({str(p.relative_to(project)):'sha256:'+Q.digest(p) for p in (project/'rtl').iterdir()})
    for key in ('POST_GLOBAL_PLACE_TCL','FASTROUTE_TCL','VERILOG_INCLUDE_DIRS'):
        for item in config.get(key,'').split():
            p=Path(item)
            for f in (p.rglob('*') if p.is_dir() else [p]):
                if f.is_file():immutable[str(f)]='sha256:'+Q.digest(f)
    allow=M.allowlist(config)
    manifest={'schema':'t2a-task-envelope-v1','task':task,'split':'DEV','family':family,
        'platform':platform,'source_project':str(project),'initial_config':config,
        'initial_config_sha256':Q.digest(project/'constraints/config.mk'),
        'expected_clock_period_ns':float(clock[1]),'stratum':'EXPOSED_DEV_VALIDATION',
        'immutable':{'knobs':sorted(set(config)-set(allow)),'files':immutable},'allowlist':allow,
        'area':{k:{'ref':areas[k],'ref_stage':'initial_2_1_floorplan','unit':'um^2',
                  'reference_file':str(reference),'reference_sha256':Q.digest(reference),
                  'max_rel_increase':M.CAPS[k]} for k in M.CAPS},
        'require_execution_binding':True,'numeric_caps_status':'USER_APPROVED_FROZEN',
        'formal_launch_allowed':False,'source_input_hashes':input_hashes}
    path=project/'manifest.json';Q.save(path,manifest)
    inventory={'schema':'t2a-real-dev-inventory-v1','status':'FROZEN_DEV','split':'DEV',
        'source_sha256':A.SOURCE_SHA,'paper_data':False,
        'tasks':[{'task':task,'family':family,'status':'ADMITTED_DEV',
                  'manifest':str(path),'manifest_sha256':Q.digest(path)}],
        'input_hashes':input_hashes,'wall_s':time.time()-start,'cpu_s':time.process_time()-cpu}
    Q.save(output/'inventory.json',inventory)
    return output/'inventory.json'


def prepare(root,parallel=False,mechanism_policy=None):
    if root.exists():raise ValueError('No overwriting prior DEV registrations')
    inventory=inputs(root.with_name(root.name+'-inputs'))
    B.prepare(root,inventory,CONTROLS,None,STORE,split='DEV',
              dev_cpu_slots=SEVEN_CPU_SLOTS if parallel else None,mechanism_policy=mechanism_policy)


def collect(root):
    reg=B.preflight(root,split='DEV');records=[];missing=[]
    for row in reg['plan']:
        name=row['task'].replace('/','__')+'__'+row['arm']+'__r'+str(row['repeat'])
        project=root/'episodes'/name;path=project/'episode.json'
        if not path.exists():missing.append(name);continue
        data=json.loads(path.read_text());events=[json.loads(l) for l in
            (project/'reports/resource_ledger.jsonl').read_text().splitlines() if l]
        requests=[e for e in events if e.get('type')=='llm_request']
        retrieved=[e for e in events if e.get('type')=='memory_retrieval']
        exercised=row['arm'] not in B.PROMPT_ARMS or bool(requests and retrieved)
        if row['arm']=='AMEM':exercised=exercised and any(e.get('role')=='auxiliary' for e in requests)
        terminal_status=B.T.terminal_status(data.get('final') or {})
        terminal_verified=(terminal_status in ('ACCEPTED','NOT_ACCEPTED') and terminal_status==data.get('status'))
        passed=(data.get('split')=='DEV' and data.get('paper_data') is False
                and data.get('status') in ('ACCEPTED','NOT_ACCEPTED')
                and data.get('accounting',{}).get('passed') is True
                and data.get('token_usage',{}).get('measurement_complete') is True and exercised and terminal_verified)
        records.append({'arm':row['arm'],'task':TASK,'episode':str(path),'episode_sha256':Q.digest(path),
            'status':data.get('status'),'accepted':data.get('accepted'),
            'accounting':data.get('accounting'),'token_usage':data.get('token_usage'),
            'online_memory_exercised':exercised,'terminal_verification_complete':terminal_verified,
            'resolved_terminal_status':terminal_status,'criteria_passed':bool(passed)})
    ok=(not missing and len(records)==7 and all(r['criteria_passed'] for r in records))
    receipt={'schema':'t2a-real-seven-arm-dev-v1','validation_kind':'REAL_PROVIDER_AND_EDA',
        'real_seven_arm_dev_validated':ok,'split':'DEV','paper_data':False,'records':records,
        'missing':missing,'online_policy':B.T.online_policy(),
        'runtime_hashes':reg['runtime_hashes'],'external_hashes':reg['external_hashes'],
        'mechanism_policy':reg.get('mechanism_policy'),
        'control_hashes':reg['control_hashes'],'registration_sha256':Q.digest(root/'registration.json'),
        'phase_binding':reg['phase_binding'],'no_retry':True,'formal_launch_allowed':False}
    Q.save(root/'dev-validation.json',receipt)
    print(A.stable({'real_seven_arm_dev_validated':ok,'records':len(records),'missing':missing}))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command',choices=('prepare','batch','collect'));ap.add_argument('--root',type=Path,required=True)
    ap.add_argument('--parallel',action='store_true',help='Freeze seven disjoint DEV slots at preparation only')
    args=ap.parse_args();root=args.root.resolve()
    if args.parallel and args.command!='prepare':ap.error('--parallel is only valid when preparing a new package')
    if args.command=='prepare':prepare(root,parallel=args.parallel)
    elif args.command=='batch':B.batch(root,split='DEV');collect(root)
    else:collect(root)
