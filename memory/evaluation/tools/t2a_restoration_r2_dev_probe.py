#!/usr/bin/env python3
"""Two no-LLM clean flows on an exposed DEV design; never Table 2 data.

Diagnostic fixture: repeat the known picorv32/u89 placement infeasibility,
simulate rejected config/report files, and verify the charged clean recovery
can establish a repairable partial state without accepting stale signoff.
No TEST inputs, provider key, frozen store or prior run is modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO/'memory/scripts'))
import t2a_runtime as T
import t2a_envelope as E
import t2a_task_manifests as M
import r2g_memory_eval as H

SOURCE=Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval/sandbox-f/design_cases/picorv32__sky130hd__u89')


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p,obj):
    p.write_text(json.dumps(obj,sort_keys=True,indent=2)+'\n')


def main(root):
    root.mkdir(parents=True,exist_ok=False)
    project=root/'project'
    for name in ('rtl','constraints','reports','backend','input','drc','lvs','rcx'):
        (project/name).mkdir(parents=True)
    text=(SOURCE/'constraints/config.mk').read_text()
    old=E.effective_config(text)
    assert old['CORE_UTILIZATION']=='89' and old['PLATFORM']=='sky130hd'
    sources=[SOURCE/'constraints/config.mk',SOURCE/'constraints/constraint.sdc']
    for item in old['VERILOG_FILES'].split():
        path=Path(item);sources.append(path)
        shutil.copy2(path,project/'rtl'/path.name)
        text=text.replace(str(path),str(project/'rtl'/path.name))
    text=text.replace(old['SDC_FILE'],str(project/'constraints/constraint.sdc'))
    if old.get('POST_GLOBAL_PLACE_TCL'):
        hook=REPO/'r2g-skills/signoff-loop/scripts/flow/orfs_hooks'/Path(old['POST_GLOBAL_PLACE_TCL']).name
        assert hook.is_file();text=text.replace(old['POST_GLOBAL_PLACE_TCL'],str(hook))
    (project/'constraints/config.mk').write_text(text)
    shutil.copy2(SOURCE/'constraints/constraint.sdc',project/'constraints/constraint.sdc')
    cfg=E.effective_config(text)
    immutable={str(p.relative_to(project)):'sha256:'+digest(p) for p in (project/'rtl').iterdir()}
    immutable['constraints/constraint.sdc']='sha256:'+digest(project/'constraints/constraint.sdc')
    manifest={'task':'picorv32/u89','split':'DEV_DIAGNOSTIC','paper_data':False,
              'initial_config':cfg,'immutable':{'knobs':sorted(set(cfg)-set(M.allowlist(cfg))),'files':immutable},
              'allowlist':M.allowlist(cfg),'require_execution_binding':True,
              'area':{},'note':'Provenance/continuation probe only; no final acceptance is claimed.'}
    save(project/'manifest.json',manifest)
    save(root/'budget.json',{'budget_id':'restoration-r2-no-llm-probe',
        'caps':{'repair_attempts':3,'llm_requests':0,'tokens_in':0,'tokens_out':0}})
    runtime=[]
    for directory in ('memory/scripts','memory/tehm','r2g-skills/signoff-loop/scripts','r2g-skills/signoff-loop/knowledge'):
        runtime += [p for p in (REPO/directory).rglob('*') if p.is_file() and p.suffix in ('.py','.sh','.tcl','.mk')]
    files={str(p):digest(p) for p in sources+runtime}
    save(root/'registration.json',{'registered_at':time.time(),'split':'DEV_DIAGNOSTIC','paper_data':False,
        'source':str(SOURCE),'files':files,'task':'picorv32/u89','llm_requests':0,'planned_flows':2,
        'no_retry':True,'initial_pressure':cfg,'cpu_affinity':sorted(os.sched_getaffinity(0)),
        'max_flow_watchdog_s':1200,'native_stage_watchdog_s':600,
        'simulation':'Only rejected project files/unsafe marker simulated; both ORFS flows are real.',
        'required':['known initial infeasibility','fresh charged full-clean partial restoration',
                    'immutable inputs unchanged','no cached signoff','no extra repair opportunity',
                    'complete actual cost record','runtime hashes unchanged']})
    for key in list(os.environ):
        if key.startswith(('R2G_BUDGET','R2G_PHASE','R2G_LEDGER','TEHM_')) or key in ('R2G_TASK_MANIFEST','FROM_STAGE'):
            os.environ.pop(key,None)
    os.environ['R2G_BUDGET_FILE']=str(root/'budget.json')
    H.SKILL=REPO/'r2g-skills/signoff-loop';H.FLOW=H.SKILL/'scripts/flow';H.EVAL=root
    H.CFG={'platform':'sky130hd'};H._EPISODE['ledger']=project
    env=H.base_env('none')
    env.update(R2G_TASK_MANIFEST=str(project/'manifest.json'),
        R2G_ENV_FILE=str(H.SKILL/'references/env.local.sh'),R2G_JOURNAL_DB=str(project/'journal.sqlite'),
        R2G_TEHM_ROOT=str(REPO/'memory'),R2G_BUDGET_COMPONENT='initial',
        ORFS_TIMEOUT='600',CUDA_VISIBLE_DEVICES='',PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=str(REPO/'memory')+':'+str(REPO/'memory/scripts'))
    started=time.time();result={'paper_data':False,'split':'DEV_DIAGNOSTIC','status':'RUNNING'}
    save(root/'result.json',result)
    try:
        rc=H.run(['bash',H.FLOW/'run_orfs.sh',project,'sky130hd'],env,project/'eval_arm.log',1200)
        result['initial_rc']=rc
        H.run(['python3',H.SKILL/'scripts/extract/extract_ppa.py',project,project/'reports/ppa.json'],env,project/'eval_arm.log',600)
        baseline=T.partial_execution_proof(project,env=env);result['baseline']=baseline
        if rc!=2 or not baseline:raise RuntimeError('Initial known infeasibility was not established; no tuning/retry')
        snapshot=T.snapshot_state(project,0)
        # Explicit diagnostic simulation, not a real candidate or model turn.
        (project/'constraints/config.mk').write_text(text+'\nexport CORE_UTILIZATION = 60\n')
        (project/'constraints/constraint.sdc').write_text('set clk_period 999\n')
        T.restore(project,snapshot,execution_changed=True)
        save(project/'reports/acceptance.json',{'accepted':True,'diagnostic_stale_fixture':True})
        save(project/'reports/drc.json',{'status':'clean','run_tag':'DIAGNOSTIC_REJECTED_STALE'})
        recovery=T.recover_execution(H,project,env);result['recovery']=recovery
        events=[json.loads(l) for l in (project/'reports/resource_ledger.jsonl').read_text().splitlines()]
        accounting=T.accounting_check(events,list((project/'backend').glob('RUN_*')))
        result.update(accounting=accounting,token_usage=T.request_token_totals(events),
            tool_calls=sum(e.get('type')=='tool_call' for e in events),
            checks={'partial_continuation':recovery.get('execution_recovery')=='PARTIAL_VERIFIED',
              'never_accepted':recovery.get('accepted') is False and T.terminal_status(recovery)=='UNVERIFIED_OR_SAFETY_STOP',
              'no_cached_signoff':not (project/'reports/acceptance.json').exists() and not (project/'reports/drc.json').exists(),
              'immutable_inputs':E.check_config(manifest,E.effective_config((project/'constraints/config.mk').read_text()),project=project,env=env)['ok'],
              'exact_two_flows':accounting['flows']==2,
              'zero_llm':accounting['llm_requests']==accounting['repair_attempts']==0,
              'accounting':accounting['passed'],
              'runtime_and_source_hashes':all(digest(Path(p))==sha for p,sha in files.items())})
        result['status']='PASS' if all(result['checks'].values()) else 'FAIL'
    except Exception as exc:
        result.update(status='FAIL',error=str(exc))
    finally:
        result['wall_s']=time.time()-started;save(root/'result.json',result)
    return 0 if result['status']=='PASS' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,required=True)
    raise SystemExit(main(parser.parse_args().root))
