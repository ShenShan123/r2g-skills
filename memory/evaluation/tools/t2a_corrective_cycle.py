#!/usr/bin/env python3
"""Fail-closed DEV -> independent audit -> complete corrective formal rerun.

Preparation sends nothing. The frozen background operator runs each phase once,
only after the full test/native gates. Prior episodes and shared payment banks
are never reset, retried, merged, signalled or deleted.
"""
import argparse
import fcntl
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def save(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,sort_keys=True,indent=2,allow_nan=False)+'\n');tmp.replace(path)


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def check_tests(path, runtime_hashes):
    data=read(path)
    if (data.get('passed') is not True or data.get('runtime_hashes')!=runtime_hashes
            or not data.get('checks') or not all(data['checks'].values())):
        raise RuntimeError('Complete tests/native preparation are not bound to this runtime')
    return data


def review_junit(path, minimum=1795):
    suites=ET.parse(path).getroot().findall('.//testsuite')
    if not suites:raise RuntimeError('No complete regression suites in JUnit receipt')
    total=sum(int(s.get('tests','0')) for s in suites)
    failures=sum(int(s.get('failures','0'))+int(s.get('errors','0')) for s in suites)
    cases=sum(len(s.findall('testcase')) for s in suites)
    if total<minimum or cases!=total or failures:
        raise RuntimeError('Combined regression suite is incomplete or failed')
    return {'tests':total,'skipped':sum(int(s.get('skipped','0')) for s in suites),
            'failures_or_errors':failures,'xml_sha256':digest(path)}


def await_tests(a):
    """Durable wait; never sends a request or reruns a test to clear a failure."""
    dev=a.dev.resolve();marker=dev/'pending-cycle-start.json'
    if marker.exists():raise RuntimeError('A pending corrective cycle already exists; no implicit retry')
    reg=read(dev/'registration.json');repo=Path('/data1/zhangdy/tehm-integration')
    source_hashes={str(repo/k):h for k,h in reg['runtime_hashes'].items()}
    files={str(p):digest(p) for p in Path(__file__).parent.glob('*.py')}
    operator_xml=dev/'operator-checks.xml'
    operator_junit=review_junit(operator_xml,minimum=12)
    files[str(operator_xml)]=digest(operator_xml)
    files[str(a.contract.resolve())]=digest(a.contract)
    def unchanged():
        if any(digest(p)!=h for p,h in {**source_hashes,**files}.items()):
            raise RuntimeError('Tested runtime or frozen operator changed while waiting')
    unchanged()
    save(marker,{'started':time.time(),'pid':os.getpid(),'dev_registration_sha256':digest(dev/'registration.json'),
        'tests_xml':str(a.tests),'target_cycle':str(a.root),'target_formal':str(a.formal),
        'runtime_hashes':reg['runtime_hashes'],'operator_files':files,'no_retry':True})
    try:
        retries=0
        while True:
            unchanged()
            save(dev/'pending-cycle-progress.json',{'stage':'WAITING_FOR_COMPLETE_REGRESSION_XML','time':time.time(),
                                                    'new_model_requests':0})
            if a.tests.exists():
                try:
                    junit=review_junit(a.tests);break
                except ET.ParseError:
                    retries+=1
                    if retries>=3:raise RuntimeError('Incomplete/corrupt regression XML')
            time.sleep(30)
        unchanged()
        receipt=dev/'unit-validation.json'
        if receipt.exists():raise RuntimeError('Test receipt already exists; no overwrite')
        save(receipt,{'passed':True,'runtime_hashes':reg['runtime_hashes'],'junit':junit,
            'operator_junit':operator_junit,'tests_xml':str(a.tests),
            'checks':{'combined_suites_passed':True,'operator_gates_passed':True,'runtime_matches_tested_source':True}})
        prepare(a.root.resolve(),dev,a.formal.resolve(),a.prior.resolve(),receipt,
                [r.resolve() for r in a.native],a.contract.resolve())
        save(dev/'pending-cycle-progress.json',{'stage':'CORRECTIVE_CYCLE_REGISTERED','time':time.time(),
                                                'cycle_root':str(a.root)})
        subprocess.run([sys.executable,'-u',str(a.root.resolve()/'operator/t2a_corrective_cycle.py'),
                        'run','--root',str(a.root.resolve())],check=True)
    except Exception as exc:
        save(dev/'pending-cycle-progress.json',{'stage':'STOPPED_NO_RETRY','time':time.time(),'error':str(exc)})
        raise


def native_items(roots):
    return [{'case':read(p/'registration.json')['case'],'root':str(p),
             'registration_sha256':digest(p/'registration.json'),'result_sha256':digest(p/'result.json')}
            for p in roots]


def phase_summary(dev, ledger, budget):
    env=dict(os.environ);env.update(R2G_LEDGER_PATH=str(ledger),R2G_BUDGET_FILE=str(budget))
    code=dev/'code/r2g-skills/signoff-loop/scripts/flow/resource_ledger.py'
    proc=subprocess.run([sys.executable,str(code),'summary',str(dev),'--json'],env=env,
                        capture_output=True,text=True,check=True,timeout=60)
    data=json.loads(proc.stdout)
    if data['open_holds']:raise RuntimeError('Payment bank has unfinished requests')
    return data


def require_payment(data, planned_requests):
    spent=data['budget_spent'];caps=data['caps']
    if not isinstance(spent.get('llm_requests'),(int,float)) or caps['llm_requests']-spent['llm_requests']<planned_requests:
        raise RuntimeError('Insufficient already-authorized requests; no reset or expansion')
    tokens=[spent.get(k) for k in ('tokens_in','tokens_out')]
    if not all(isinstance(v,(int,float)) for v in tokens) or data['combined_tokens_cap']-sum(tokens)<=0:
        raise RuntimeError('Payment usage/cap unknown or exhausted')


def prepare(root,dev,formal,prior,tests,natives,contract):
    if root.exists() or formal.exists():raise RuntimeError('New roots required; no implicit overwrite/retry')
    sys.path.insert(0,str(dev/'code/memory/scripts'))
    B=importlib.import_module('t2a_batch');reg=B.preflight(dev,'DEV')
    if (dev/'batch-start.json').exists():raise RuntimeError('DEV already started; no implicit reuse/retry')
    check_tests(tests,reg['runtime_hashes'])
    source=Path(__file__).resolve().parent
    A=module('cycle_native_audit',source/'t2a_dev_completion_audit.py')
    items=native_items(natives)
    if not A.native_checks_bound(items,reg['runtime_hashes'],digest(dev/'registration.json')):
        raise RuntimeError('All three exact-runtime native paths must pass first')
    C=module('cycle_prior_guard',source/'t2a_formal_control.py')
    completed={'root':str(prior),'registration_sha256':digest(prior/'registration.json'),
        'completion_sha256':digest(prior/'batch-complete.json'),'results_sha256':digest(prior/'formal-results.json'),
        'supervisor':{'pid':2077020,'start_ticks':'649613589'}}
    C.verify_completed_prior({'completed_prior':completed})
    if A.owned_survivors(prior):raise RuntimeError('Prior batch still owns active tools')
    ledger=(prior/'phase/resource_ledger.jsonl').resolve();budget=prior/'phase-budget.json'
    formal_bank=phase_summary(dev,ledger,budget);require_payment(formal_bank,192)
    binding=reg['phase_binding']
    dev_bank=phase_summary(dev,Path(binding['ledger_path']),Path(binding['budget_file']));require_payment(dev_bank,24)
    root.mkdir(parents=True);(root/'operator').mkdir()
    copied={}
    for filename in ('t2a_corrective_cycle.py','t2a_dev_completion_audit.py','t2a_formal_control.py'):
        target=root/'operator'/filename;shutil.copy2(source/filename,target);copied[str(target)]=digest(target)
    shutil.copy2(contract,root/'registered-contract.md');copied[str(root/'registered-contract.md')]=digest(root/'registered-contract.md')
    prefix=ledger.read_bytes()
    save(root/'registration.json',{'schema':'t2a-consolidated-corrective-cycle-r3-v1',
        'registered_at':time.time(),'dev_root':str(dev),'dev_registration_sha256':digest(dev/'registration.json'),
        'formal_root':str(formal),'completed_prior':completed,'native_checks':items,
        'tests':str(tests),'tests_sha256':digest(tests),'files':copied,
        'formal_ledger':str(ledger),'formal_budget':str(budget),'formal_budget_sha256':digest(budget),
        'formal_prefix_bytes':len(prefix),'formal_prefix_sha256':hashlib.sha256(prefix).hexdigest(),
        'formal_bank_at_registration':formal_bank,'dev_bank_at_registration':dev_bank,
        'authorized_workflow':'User requested consolidated corrections, validation, refreeze and complete rerun.',
        'planned_formal_episodes':56,'planned_formal_max_requests':192,'planned_dev_max_requests':24,
        'TEST_exposure':'F1/F2 failures were inspected to correct shared execution; not a new blind confirmation.',
        'no_implicit_retry':True,'no_budget_reset':True})
    print(json.dumps({'status':'REGISTERED_READY','root':str(root),'new_provider_requests':0}))


def run(root):
    lock=(root/'cycle.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    grant=read(root/'registration.json')
    if (root/'cycle-start.json').exists():raise RuntimeError('Cycle already started; no implicit retry')
    for name,sha in grant['files'].items():
        if digest(name)!=sha:raise RuntimeError('Registered operator/contract changed')
    if str(Path(__file__).resolve()) not in grant['files']:raise RuntimeError('Run the frozen operator')
    dev=Path(grant['dev_root']);formal=Path(grant['formal_root'])
    if digest(dev/'registration.json')!=grant['dev_registration_sha256']:raise RuntimeError('DEV registration changed')
    sys.path.insert(0,str(dev/'code/memory/scripts'))
    B=importlib.import_module('t2a_batch');reg=B.preflight(dev,'DEV')
    if digest(grant['tests'])!=grant['tests_sha256']:raise RuntimeError('Test receipt changed')
    check_tests(grant['tests'],reg['runtime_hashes'])
    A=module('cycle_completion_audit',root/'operator/t2a_dev_completion_audit.py')
    C=module('cycle_formal_guard',root/'operator/t2a_formal_control.py')
    if not A.native_checks_bound(grant['native_checks'],reg['runtime_hashes'],grant['dev_registration_sha256']):
        raise RuntimeError('Native validation changed')
    C.verify_completed_prior(grant)
    save(root/'cycle-start.json',{'started':time.time(),'pid':os.getpid(),
                                 'registration_sha256':digest(root/'registration.json')})
    def stage(name,**extra):save(root/'progress.json',{'stage':name,'time':time.time(),**extra})
    try:
        stage('REAL_SEVEN_ARM_DEV_RUNNING')
        with (dev/'worker.log').open('x') as output:
            subprocess.run([sys.executable,'-u',str(dev/'code/memory/scripts/t2a_dev.py'),'batch','--root',str(dev)],
                stdout=output,stderr=subprocess.STDOUT,check=True)
        save(dev/'completion-audit-registration.json',{
            'registration_sha256':digest(dev/'registration.json'),
            'auditor_sha256':digest(root/'operator/t2a_dev_completion_audit.py'),
            'native_checks':grant['native_checks']})
        audit=A.audit(dev)
        if not audit['passed']:raise RuntimeError('Independent real DEV/native audit did not pass')
        stage('DEV_AND_NATIVE_VALIDATION_PASSED')
        bank=Path(grant['formal_ledger']);prefix=bank.read_bytes()
        if hashlib.sha256(prefix[:grant['formal_prefix_bytes']]).hexdigest()!=grant['formal_prefix_sha256']:
            raise RuntimeError('Prior formal payment prefix changed')
        if digest(grant['formal_budget'])!=grant['formal_budget_sha256']:raise RuntimeError('Payment ceiling changed')
        require_payment(phase_summary(dev,bank,Path(grant['formal_budget'])),192)
        # Q.REPO belongs to the private DEV snapshot, not an editable worktree.
        B.prepare(formal,Path(grant['completed_prior']['root'])/'inventory.json',dev/'controls',
                  dev/'dev-validation.json',dev/'store')
        B.preflight(formal,'TEST')
        if (formal/'phase-budget.json').read_bytes()!=Path(grant['formal_budget']).read_bytes():
            raise RuntimeError('Formal bank allocation changed during preparation')
        (formal/'phase').symlink_to(bank.parent,target_is_directory=True)
        (formal/'operator').mkdir()
        controller=formal/'operator/formal_control.py'
        shutil.copy2(root/'operator/t2a_formal_control.py',controller)
        prefix=bank.read_bytes()
        save(formal/'formal-control-registration.json',{
            'controller_sha256':digest(controller),'registration_sha256':digest(formal/'registration.json'),
            'original_ledger':str(bank),'prior_ledger_bytes':len(prefix),
            'prior_ledger_sha256':hashlib.sha256(prefix).hexdigest(),
            'budget_sha256':digest(formal/'phase-budget.json'),'dev_root':str(dev),
            'dev_receipt_sha256':digest(dev/'dev-validation.json'),
            'dev_audit_sha256':digest(dev/'completion-audit.json'),
            'completed_prior':grant['completed_prior'],'result_schema':'t2a-corrective-formal-results-f3-v1',
            'TEST_exposure':grant['TEST_exposure'],'cycle_registration_sha256':digest(root/'registration.json')})
        stage('CORRECTED_FORMAL_RUNNING',formal_root=str(formal),
              formal_registration_sha256=digest(formal/'registration.json'))
        with (formal/'worker.log').open('x') as output:
            subprocess.run([sys.executable,'-u',str(controller),'--root',str(formal)],
                           stdout=output,stderr=subprocess.STDOUT,check=True)
        result=read(formal/'formal-results.json')
        if not result.get('execution_complete') or not result.get('data_audit_passed'):
            raise RuntimeError('Complete formal accounting/result audit did not pass')
        stage('COMPLETE',formal_root=str(formal),results_sha256=digest(formal/'formal-results.json'))
    except Exception as exc:
        stage('STOPPED_NO_RETRY',error=str(exc));raise


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=('prepare','run','await-tests'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--dev',type=Path)
    p.add_argument('--formal',type=Path);p.add_argument('--prior',type=Path)
    p.add_argument('--tests',type=Path);p.add_argument('--native',type=Path,action='append',default=[])
    p.add_argument('--contract',type=Path)
    a=p.parse_args()
    if a.command=='run':run(a.root.resolve())
    elif a.command=='await-tests':
        if not all((a.dev,a.formal,a.prior,a.tests,a.contract)):p.error('Waiting requires all registered inputs')
        await_tests(a)
    else:
        if not all((a.dev,a.formal,a.prior,a.tests,a.contract)):p.error('Preparation requires all registered inputs')
        prepare(a.root.resolve(),a.dev.resolve(),a.formal.resolve(),a.prior.resolve(),a.tests.resolve(),
                [r.resolve() for r in a.native],a.contract.resolve())
