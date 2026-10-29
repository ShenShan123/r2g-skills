"""Freeze the user-authorized skip-and-defer policy, never modifying raw results."""
import copy
import fcntl
import json
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from admission import active_registration,admission_blockers,sha,unresolved_unknowns
from batch import continuation_plan,roster
from provision import record


def main():
    with (ROOT/'reports/dispatcher.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        freeze()


def freeze():
    pointer=ROOT/'inputs/active-registration.json'
    previous_pointer=pointer.read_text()
    previous=json.loads(previous_pointer)
    if previous['path']!='inputs/registration-v3.json':
        raise ValueError('unexpected active runtime; never overwrite another run')
    base=active_registration(ROOT)
    changed={rel for rel,h in base['files'].items() if sha(ROOT/rel)!=h}
    allowed={'adapters/admission.py','adapters/batch.py','adapters/run_swe.py'}
    if changed!=allowed:
        raise ValueError('unexpected runtime delta: '+str(changed))
    tests=ROOT/'reports/skip-policy-final.xml'
    tests_sha=sha(tests)
    cases=list(ET.parse(tests).getroot().iter('testcase'))
    if len(cases)!=33 or any(any(c.find(k) is not None for k in ('failure','error','skipped')) for c in cases):
        raise ValueError('final 33-test regression must pass before freezing')
    tasks=roster()
    preserved=[t['instance_id'] for t in tasks[:2]]
    if preserved!=['sphinx-doc__sphinx-9673','mwaskom__seaborn-3187']:
        raise ValueError('original fixed task order changed')
    protected={};links={}
    for instance in preserved:
        for arm in ('native','tehm'):
            directory=ROOT/'hosts/swe-agent'/arm/'episodes'/instance
            if not (directory/'result.json').exists():
                raise ValueError('preserved episode not terminal')
            for path in sorted(directory.rglob('*')):
                if path.is_symlink():
                    links[str(path.relative_to(ROOT))]=str(path.readlink())
                elif path.is_file():
                    protected[str(path.relative_to(ROOT))]=sha(path)
    old_auth=json.loads((ROOT/base['continuation_authorization']).read_text())
    auth=ROOT/'inputs/skip-interruptions-v5-authorization.json'
    target=ROOT/'inputs/registration-v5.json'
    if auth.exists() or target.exists():
        raise ValueError('skip policy already frozen; never overwrite')
    authorization={
        'user_authorization':'中断不要打断，先跳过，等跑完再单独重试处理',
        'recorded_at':time.time(),'scope':'current SWE20 queue only; original fixed 20 tasks and native/TEHM arms',
        'interruption_policy':'skip_local_interruptions_no_retry',
        'preserved_tasks':preserved,
        'remaining_episodes':[[t['instance_id'],a] for t in tasks[2:] for a in ('native','tehm')],
        'unknown_acknowledgements':old_auth['unknown_acknowledgements'],
        'unknown_policy':'raw actual usage remains UNKNOWN; stop affected episode, defer and continue other lane/later tasks',
        'retry_policy':'no request/episode retries during original queue; distinct retry round after completion, originals preserved',
        'qualification':'failed preparation deferred without paid calls; no replacement or weakening of acceptance',
        'global_stop':'identity, authorization/permission, frozen runtime or evidence integrity faults remain global blockers',
        'cumulative_token_money_caps':None}
    value=copy.deepcopy(base)
    value.update(epoch='skip-local-interruptions-v5',created=time.time(),
        previous_registration=previous['path'],previous_registration_sha256=previous['sha256'],
        interruption_policy=authorization['interruption_policy'],
        continuation_authorization=str(auth.relative_to(ROOT)),
        protected_first_pair=protected,protected_first_pair_symlinks=links,
        preserved_completed_episodes=4,remaining_on_this_runtime=36,
        runtime_delta=sorted(allowed),
        unknown_policy=authorization['unknown_policy'],
        repair_algorithm_changes=False,native_model_tools_acceptance_changes=False)
    # This report is new and append-only. Old authorization/registrations/results
    # remain present and retain their own immutable hashes.
    record(auth,authorization)
    files=list((ROOT/'adapters').glob('*.py'))+list((ROOT/'tests').glob('*.py'))+[
        auth,tests,Path(__file__),ROOT/'operators/qualify_skip_interruptions_v5.py',
        ROOT/'operators/qualify_remaining_mixed_v4.py']
    for path in files:
        value['files'][str(path.relative_to(ROOT))]=sha(path)
    if sha(tests)!=tests_sha:
        raise ValueError('test receipt changed while freezing')
    continuation_plan(ROOT,value,tasks)
    record(target,value)
    with (ROOT/'reports/continuation-v3-active-pointer.json').open('x') as out:
        out.write(previous_pointer)
    record(pointer,{'path':str(target.relative_to(ROOT)),'sha256':sha(target)})
    from run_swe import check_freeze
    check_freeze()
    if admission_blockers(ROOT):
        raise ValueError('global admission remains blocked')
    receipt={'status':'FROZEN_READY','epoch':value['epoch'],'registration_sha256':sha(target),
        'tests_passed':len(cases),'preserved_episodes':4,'remaining_unstarted':36,
        'unknowns_retained':unresolved_unknowns(ROOT),'automatic_retries':0,
        'protected_files':len(protected),'created':time.time()}
    record(ROOT/'reports/skip-interruptions-v5-freeze.json',receipt)
    print(json.dumps(receipt))


if __name__=='__main__':
    main()
