#!/usr/bin/env python3
"""Registered no-model native checks of exposed restoration paths, never paper data.

Only owned config paths are rebased. Old episodes, constraints, area references
and software are never edited. Each invocation creates a new diagnostic root,
executes exactly two real flows, and fails without retry if the path differs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

F2=Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/t2a-formal-f2-20261013')
CASES={
    'cts_partial':(F2/'episodes/gcd__u80__NB__r1','reports/t2a-rollback/turn-2/constraints/config.mk','EXPOSED_TEST_DIAGNOSTIC'),
    'completed_area':(F2/'episodes/gcd__default__NB__r1','reports/t2a-rollback/turn-1/constraints/config.mk','EXPOSED_TEST_DIAGNOSTIC'),
    'place_partial':(F2.parent/'t2a-restoration-r2-dev-probe-20261013-c/project','constraints/config.mk','EXPOSED_DEV_DIAGNOSTIC'),
    'task_entry':(F2.parent/'t2a-restoration-r2-dev-probe-20261013-c/project','constraints/config.mk','EXPOSED_DEV_DIAGNOSTIC'),
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, data):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(data,sort_keys=True,indent=2,allow_nan=False)+'\n')
    tmp.replace(path)


def project_path(root, case):
    # ORFS publishes configs under a global design/variant directory. A common
    # basename such as "project" would collide across concurrent diagnostics.
    return root/(root.name.replace('-','_')+'__'+case)


def main(root, runtime, case):
    if root.exists():raise RuntimeError('Existing diagnostic; no overwrite/retry')
    runtime=runtime.resolve()
    sys.path.insert(0,str(runtime/'code/memory/scripts'))
    import t2a_runtime as T
    import t2a_envelope as E
    import t2a_qualify as Q
    import r2g_memory_eval as H
    registration=json.loads((runtime/'registration.json').read_text())
    for name,sha in registration['files'].items():
        if digest(name)!=sha:raise RuntimeError('Frozen runtime/input drift: '+name)
    source, config_relative, split=CASES[case]
    cfg_source=source/config_relative
    man_source=source/'manifest.json'
    root.mkdir(parents=True,exist_ok=False)
    project=project_path(root,case)
    for name in ('constraints','rtl','backend','reports','input','drc','lvs','rcx'):
        (project/name).mkdir(parents=True)
    shutil.copytree(source/'rtl',project/'rtl',dirs_exist_ok=True)
    shutil.copy2(source/'constraints/constraint.sdc',project/'constraints/constraint.sdc')
    text=cfg_source.read_text().replace(str(source)+'/',str(project)+'/')
    (project/'constraints/config.mk').write_text(text)
    man=json.loads(man_source.read_text())
    man['initial_config']={k:v.replace(str(source)+'/',str(project)+'/') for k,v in man['initial_config'].items()}
    man.update(source_project=str(project),split=split,paper_data=False,formal_launch_allowed=False)
    save(project/'manifest.json',man)
    source_files=[Path(__file__),cfg_source,man_source,source/'constraints/constraint.sdc',
                  *[p for p in (source/'rtl').rglob('*') if p.is_file()]]
    source_hashes={str(p):digest(p) for p in source_files}
    save(root/'budget.json',{'budget_id':'native-restoration-check',
         'caps':{'llm_requests':0,'tokens_in':0,'tokens_out':0,'repair_attempts':3},'reserve':{}})
    save(root/'registration.json',{'schema':'t2a-native-restoration-check-v1',
        'registered_at':time.time(),'case':case,'split':split,'paper_data':False,
        'runtime_root':str(runtime),'runtime_registration_sha256':digest(runtime/'registration.json'),
        'runtime_hashes':registration['runtime_hashes'],'source_files':source_hashes,
        'operator_sha256':digest(__file__),'cpu_affinity':sorted(os.sched_getaffinity(0)),
        'planned_flows':2,'llm_requests':0,'no_retry':True,
        'simulation':'Rejected config/SDC/stale reports only; initial and recovery EDA are real.',
        'constraints':'Original task area references/caps and immutable inputs are unchanged.'})
    for key in list(os.environ):
        if key.startswith(('R2G_BUDGET','R2G_PHASE','R2G_LEDGER','TEHM_')) or key in ('R2G_TASK_MANIFEST','FROM_STAGE'):
            os.environ.pop(key,None)
    os.environ['R2G_BUDGET_FILE']=str(root/'budget.json')
    H.SKILL=runtime/'code/r2g-skills/signoff-loop';H.FLOW=H.SKILL/'scripts/flow'
    H.EVAL=root;H.CFG={'platform':man.get('platform','sky130hd')};H._EPISODE['ledger']=project
    env=H.base_env('none')
    env.update(R2G_TASK_MANIFEST=str(project/'manifest.json'),R2G_ENV_FILE=str(H.SKILL/'references/env.local.sh'),
        R2G_JOURNAL_DB=str(project/'journal.sqlite'),R2G_TEHM_ROOT=str(runtime/'code/memory'),
        PYTHONPATH=str(runtime/'code/memory')+':'+str(runtime/'code/memory/scripts'),
        R2G_BUDGET_COMPONENT='initial',ORFS_TIMEOUT='1200',CUDA_VISIBLE_DEVICES='',PYTHONDONTWRITEBYTECODE='1')
    began=time.time();result={'status':'RUNNING','paper_data':False,'split':split,'case':case}
    save(root/'result.json',result)
    try:
        rc=H.run(['bash',H.FLOW/'run_orfs.sh',project,H.CFG['platform']],env,project/'eval_arm.log',2400)
        result['initial_rc']=rc
        H.run(['python3',H.SKILL/'scripts/extract/extract_ppa.py',project,project/'reports/ppa.json'],env,project/'eval_arm.log',600)
        if case=='completed_area':
            initial=H.final_verdict(project,env,project/'eval_arm.log');result['initial']=initial
            if rc!=0 or initial.get('envelope',{}).get('status')!='FAIL':
                raise RuntimeError('Registered completed/area-failing path not reproduced; no retuning')
        else:
            proof=T.partial_execution_proof(project,env=env);result['baseline']=proof
            wanted='DPL-0038' if case=='cts_partial' else 'FLW-0024'
            if rc!=2 or not proof or proof['error_code']!=wanted:
                raise RuntimeError('Registered '+wanted+' path not reproduced; no retuning')
        snap=T.task_entry_snapshot(project,env) if case=='task_entry' else T.snapshot_state(project,0,env)
        (project/'constraints/config.mk').write_text(text+'\nexport CORE_UTILIZATION = 20\n')
        (project/'constraints/constraint.sdc').write_text('set clk_period 999\n')
        if case=='task_entry':
            save(project/'reports/antenna_nonconverged.json',{'discarded_memory_branch':True})
            T.restore_task_entry(project,snap)
        else:T.restore(project,snap,execution_changed=True)
        save(project/'reports/acceptance.json',{'accepted':True,'diagnostic_stale_fixture':True})
        save(project/'reports/drc.json',{'status':'clean','diagnostic_stale_fixture':True})
        recovery=T.recover_execution(H,project,env);result['recovery']=recovery
        events=[json.loads(line) for line in (project/'reports/resource_ledger.jsonl').read_text().splitlines() if line]
        accounting=T.accounting_check(events,list((project/'backend').glob('RUN_*')))
        known_quality=(case=='completed_area')
        checks={'path_recovery':recovery.get('execution_recovery')==('VERIFIED' if known_quality else 'PARTIAL_VERIFIED'),
          'never_accepted':recovery.get('accepted') is False,
          'remaining_turns_preserved':accounting['repair_attempts']==0,
          'two_flows':accounting['flows']==2,'zero_requests':accounting['llm_requests']==0,
          'accounting':accounting['passed'],
          'no_unsafe_marker':not (project/'reports/t2a-workspace-unsafe.json').exists(),
          'immutable_inputs':E.check_config(man,E.effective_config((project/'constraints/config.mk').read_text()),project=project,env=env)['ok'],
          'source_unchanged':all(digest(p)==sha for p,sha in source_hashes.items()),
          'runtime_unchanged':all(digest(p)==sha for p,sha in registration['files'].items())}
        if known_quality:
            checks['quality_still_fails']=recovery.get('envelope',{}).get('status')=='FAIL' and T.terminal_status(recovery)=='NOT_ACCEPTED'
            checks['fresh_acceptance']=not json.loads((project/'reports/acceptance.json').read_text()).get('diagnostic_stale_fixture')
        else:
            checks['not_terminal_success']=T.terminal_status(recovery)=='UNVERIFIED_OR_SAFETY_STOP'
            checks['stale_evidence_removed']=not (project/'reports/acceptance.json').exists() and not (project/'reports/drc.json').exists()
        if case=='task_entry':
            checks['task_control_restored']=not (project/'reports/antenna_nonconverged.json').exists()
            checks['discarded_control_preserved']=(snap/'rejected/reports/antenna_nonconverged.json').exists()
        result.update(checks=checks,accounting=accounting,token_usage=T.request_token_totals(events),
            tool_calls=sum(e.get('type')=='tool_call' for e in events),
            checker_runs=sum(e.get('type')=='checker_run' for e in events),
            status='PASS' if all(checks.values()) else 'FAIL')
    except Exception as exc:
        result.update(status='FAIL',error=str(exc))
    finally:
        result['wall_s']=time.time()-began
        ledger=project/'reports/resource_ledger.jsonl'
        events=[json.loads(line) for line in ledger.read_text().splitlines() if line] if ledger.exists() else []
        result.update(accounting=T.accounting_check(events,list((project/'backend').glob('RUN_*'))),
            token_usage=T.request_token_totals(events),tool_calls=sum(e.get('type')=='tool_call' for e in events),
            checker_runs=sum(e.get('type')=='checker_run' for e in events))
        save(root/'result.json',result)
    print(json.dumps({'case':case,'status':result['status'],'wall_s':result['wall_s'],'error':result.get('error')}))
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--case',choices=CASES,required=True)
    a=p.parse_args();raise SystemExit(main(a.root.resolve(),a.runtime.resolve(),a.case))
