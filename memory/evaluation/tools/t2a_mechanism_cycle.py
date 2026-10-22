#!/usr/bin/env python3
"""Once-only, frozen R4 DEV/ablation sequence. Never dispatches formal TEST."""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

sys.dont_write_bytecode=True
REPO=Path('/data1/zhangdy/tehm-integration')
EVAL=Path('/data1/zhangdy/RTL/RTL_testbench/_r2g_eval')
PRIOR=EVAL/'t2a-real-seven-arm-dev-r3-20261015'


def read(path):return json.loads(Path(path).read_text())
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n');temp.replace(path)
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def junit(path):
    suites=ET.parse(path).getroot().findall('.//testsuite')
    total=sum(int(s.get('tests','0')) for s in suites)
    bad=sum(int(s.get('failures','0'))+int(s.get('errors','0')) for s in suites)
    if total<1800 or bad or sum(len(s.findall('testcase')) for s in suites)!=total:
        raise RuntimeError('Complete exact-candidate regression did not pass')
    return {'tests':total,'skips':sum(int(s.get('skipped','0')) for s in suites),'sha256':digest(path)}


def inputs(root,D,B):
    items=[];hashes={}
    for task in B.MECHANISM_DEV_TASKS:
        p=D.inputs(root/task.replace('/','__'),task)
        inv=read(p);items.extend(inv['tasks']);hashes.update(inv['input_hashes'])
    result={'schema':'t2a-mechanism-dev-inventory-v1','status':'FROZEN_DEV','split':'DEV',
            'source_sha256':D.A.SOURCE_SHA,'paper_data':False,'tasks':items,'input_hashes':hashes}
    if B.validate_dev_inventory(result):raise RuntimeError('DEV cohort identity failed')
    path=root/'inventory.json';save(path,result);return path


def prepare(root):
    sys.path.insert(0,str(REPO/'memory/scripts'))
    import t2a_batch as B
    import t2a_dev as D
    import t2a_mechanisms as M
    if root.exists():raise RuntimeError('Existing cycle; no overwrite or implicit retry')
    pause=EVAL/'t2a-corrective-cycle-r3-20261015/formal-dispatch-pause.json'
    if not pause.exists() or read(pause).get('resume_authorized') is not False:
        raise RuntimeError('Original formal dispatch pause must remain recorded')
    root.mkdir(parents=True);(root/'operator').mkdir()
    inventory=inputs(root/'inputs',D,B)
    core_policy=root/'core-policy.json'
    save(core_policy,{'schema':M.SCHEMA,'task_entry_fallback':False,'cost_priority':False,'priority_samples':[]})
    B.prepare(root/'core',inventory,D.CONTROLS,None,D.STORE,split='DEV',
              mechanism_policy=core_policy,dev_arms=('NB','TM0'))
    B.preflight(root/'core','DEV')
    files={}
    for filename in ('t2a_mechanism_cycle.py','t2a_native_restoration_check.py','t2a_dev_completion_audit.py'):
        target=root/'operator'/filename;shutil.copy2(Path(__file__).parent/filename,target)
        files[str(target)]=digest(target)
    contract=REPO/'memory/evaluation/r2g_T2a_R4_DEV_contract_20261015.md'
    shutil.copy2(contract,root/'contract.md');files[str(root/'contract.md')]=digest(root/'contract.md')
    reg=read(root/'core/registration.json')
    save(root/'registration.json',{'schema':'t2a-mechanism-cycle-r4-v1','registered_at':time.time(),
        'files':files,'core_registration_sha256':digest(root/'core/registration.json'),
        'runtime_hashes':reg['runtime_hashes'],'prior_dev':str(PRIOR),
        'prior_registration_sha256':digest(PRIOR/'registration.json'),
        'pause_file':str(pause),'pause_sha256':digest(pause),'max_new_requests':96,
        'existing_shared_bank':reg['phase_binding'],'new_formal_launch_allowed':False,
        'tested_operator_sources':{str(Path(__file__).parent/Path(p).name):h for p,h in files.items() if p.endswith('.py')},
        'planned_episodes':{'core':6,'full':6,'no-priority':6,'no-fallback':6,'final_seven_arm':7},
        'no_retry':True,'no_budget_reset':True})
    print(json.dumps({'status':'PREPARED_DEV_ONLY','root':str(root),'provider_requests':0,'EDA_flows':0}))


def run(root):
    lock=(root/'cycle.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (root/'cycle-start.json').exists():raise RuntimeError('Cycle already started; no implicit retry')
    grant=read(root/'registration.json')
    if str(Path(__file__).resolve()) not in grant['files']:raise RuntimeError('Run only frozen operator')
    def unchanged():
        for path,sha in grant['files'].items():
            if digest(path)!=sha:raise RuntimeError('Frozen operator/contract drift')
        if digest(root/'core/registration.json')!=grant['core_registration_sha256']:
            raise RuntimeError('CORE registration changed')
        if digest(grant['pause_file'])!=grant['pause_sha256']:
            raise RuntimeError('Formal dispatch pause changed')
        if (EVAL/'t2a-formal-f3-20261015').exists():raise RuntimeError('Old formal dispatch resumed')
    unchanged()
    save(root/'cycle-start.json',{'started':time.time(),'pid':os.getpid(),'registration_sha256':digest(root/'registration.json')})
    def stage(name,**data):save(root/'progress.json',{'stage':name,'time':time.time(),**data})
    try:
        stage('WAITING_FOR_FULL_TESTS_AND_PRIOR_DEV')
        while not (root/'combined-regression.xml').exists():unchanged();time.sleep(30)
        for attempt in range(3):
            try:tests=junit(root/'combined-regression.xml');break
            except ET.ParseError:
                if attempt==2:raise
                time.sleep(30)  # finish reading the receipt; never rerun a failed test
        for rel,sha in grant['runtime_hashes'].items():
            if digest(REPO/rel)!=sha:raise RuntimeError('Worktree changed after test registration')
        for path,sha in grant.get('tested_operator_sources',{}).items():
            if digest(path)!=sha:raise RuntimeError('Operator changed after test registration')
        save(root/'unit-validation.json',{'passed':True,'junit':tests,'runtime_hashes':grant['runtime_hashes']})
        prior=Path(grant['prior_dev'])
        while not (prior/'batch-complete.json').exists():unchanged();time.sleep(30)
        if digest(prior/'registration.json')!=grant['prior_registration_sha256']:
            raise RuntimeError('Original DEV registration changed')
        if read(prior/'dev-validation.json').get('real_seven_arm_dev_validated') is not True:
            raise RuntimeError('Retained R3 DEV failed its integrity/plumbing gate; inspect, never retry implicitly')
        sys.path.insert(0,str(root/'core/code/memory/scripts'))
        import t2a_batch as B
        import t2a_dev as D
        import t2a_mechanisms as M
        B.preflight(root/'core','DEV')
        audit=module('r4_independent_audit',root/'operator/t2a_dev_completion_audit.py')
        if audit.owned_survivors(prior):raise RuntimeError('Prior DEV still owns tool processes')
        binding=grant['existing_shared_bank']
        bankenv={**os.environ,'R2G_LEDGER_PATH':binding['ledger_path'],'R2G_BUDGET_FILE':binding['budget_file']}
        ledger_code=root/'core/code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
        bank=subprocess.run([sys.executable,str(ledger_code),'summary',str(root),'--json'],
            env=bankenv,capture_output=True,text=True,check=True)
        bank=json.loads(bank.stdout)
        if bank['open_holds'] or bank['caps']['llm_requests']-bank['budget_spent']['llm_requests']<96:
            raise RuntimeError('Insufficient already-authorized DEV requests or unfinished calls')
        occupied=sum(bank['budget_spent'][k] for k in ('tokens_in','tokens_out'))
        if occupied>=bank['combined_tokens_cap']:raise RuntimeError('Existing token authorization exhausted')
        save(root/'bank-before-dispatch.json',bank)
        childenv={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','CUDA_VISIBLE_DEVICES':'',
            'LD_LIBRARY_PATH':'/data1/zhangdy/Tools/tehm-toolchain/oss-cad-suite/lib'}
        def launch(command,log):
            unchanged()
            with log.open('x') as output:
                subprocess.run(command,env=childenv,stdout=output,stderr=subprocess.STDOUT,check=True)
        def native(runtime,label):
            receipts=[]
            for case in ('cts_partial','completed_area','place_partial','task_entry'):
                output=root/(label+'-'+case)
                launch(['taskset','-c',','.join(map(str,B.CPU_SLOTS[0])),sys.executable,
                        str(root/'operator/t2a_native_restoration_check.py'),'--runtime',str(runtime),
                        '--root',str(output),'--case',case],root/(label+'-'+case+'.log'))
                r=read(output/'result.json')
                if r.get('status')!='PASS' or r.get('token_usage',{}).get('llm_requests')!=0:
                    raise RuntimeError('Native diagnostic failed')
                receipts.append({'case':case,'root':str(output),
                    'registration_sha256':digest(output/'registration.json'),'result_sha256':digest(output/'result.json')})
            return receipts
        def batch(path):
            launch([sys.executable,str(path/'code/memory/scripts/t2a_batch.py'),'batch',
                    '--root',str(path),'--split','DEV'],path/'worker.log')
            if audit.owned_survivors(path):raise RuntimeError('DEV left owned tools running')
            result=M.collect_pairs(path)
            if not result['data_audit_passed']:raise RuntimeError('DEV identity/accounting/cost audit failed')
            return result
        stage('INITIAL_NATIVE_CHECKS')
        native(root/'core','native-core')
        stage('CORE_DEV_RUNNING')
        reports={'core':batch(root/'core')}
        samples=M.samples_from_dev(root/'core')
        save(root/'priority-samples.json',{'source':'audited CORE DEV only','samples':samples})
        for label,priority,fallback in [('full',True,True),('no-priority',False,True),('no-fallback',True,False)]:
            policy=root/(label+'-policy.json')
            save(policy,{'schema':M.SCHEMA,'task_entry_fallback':fallback,'cost_priority':priority,'priority_samples':samples})
            B.prepare(root/label,root/'core/inventory.json',root/'core/controls',None,root/'core/store',
                      split='DEV',mechanism_policy=policy,dev_arms=('NB','TM0'))
            stage(label.upper().replace('-','_')+'_DEV_RUNNING')
            reports[label]=batch(root/label)
        save(root/'ablation-results.json',{'schema':'t2a-mechanism-ablations-r4-v1','reports':reports,
                                         'paper_data':False,'formal_launch_allowed':False})
        if not reports['full']['dev_release_gate_passed']:
            stage('COMPLETE_DEV_NEGATIVE_NO_RELEASE',report=str(root/'ablation-results.json'));return
        final=root/'final-seven-arm'
        D.prepare(final,parallel=False,mechanism_policy=root/'full-policy.json')
        stage('EXACT_FINAL_NATIVE_CHECKS')
        receipts=native(final,'native-final')
        stage('FINAL_SEVEN_ARM_DEV_RUNNING')
        launch([sys.executable,str(final/'code/memory/scripts/t2a_dev.py'),'batch','--root',str(final)],final/'worker.log')
        save(final/'completion-audit-registration.json',{
            'registration_sha256':digest(final/'registration.json'),
            'auditor_sha256':digest(root/'operator/t2a_dev_completion_audit.py'),
            'native_checks':[r for r in receipts if r['case']!='task_entry']})
        result=audit.audit(final)
        if not result['passed']:raise RuntimeError('Independent seven-arm DEV/native gate failed')
        exercised=any((read(final_path).get('task_entry_fallback') or {}).get('executed')
                      for final_path in (root/'full/episodes').glob('*/episode.json'))
        if not exercised:raise RuntimeError('Task-level fallback not exercised by real DEV; not a pass')
        save(root/'release-freeze.json',{'status':'DEV_VALIDATED_FROZEN_NOT_FORMAL','root':str(final),
            'registration_sha256':digest(final/'registration.json'),'dev_validation_sha256':digest(final/'dev-validation.json'),
            'completion_audit_sha256':digest(final/'completion-audit.json'),
            'performance_report_sha256':digest(root/'ablation-results.json'),'formal_launch_allowed':False})
        stage('DEV_VALIDATED_FROZEN_WAITING_FOR_FORMAL_DECISION')
    except Exception as exc:
        stage('STOPPED_NO_RETRY',error=str(exc));raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=('prepare','run'));parser.add_argument('--root',type=Path,required=True)
    args=parser.parse_args();root=args.root.resolve()
    if args.command=='prepare':prepare(root)
    else:run(root)
