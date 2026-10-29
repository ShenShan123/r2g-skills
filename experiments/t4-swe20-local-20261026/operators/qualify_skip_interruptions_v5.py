"""Try each still-unprepared environment once; preserve failures and continue.

No failed preparation or Agent episode is retried by this pass. The fixed roster
is unchanged, and only baseline FAIL + reference PASS qualifies a paid episode.
"""
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'adapters'),str(ROOT/'operators')]
import provision
from admission import sha
from qualify_remaining_mixed_v4 import mixed_recipe


def main():
    original=provision.local_dependency_recipe
    def recipe(spec,upstream):
        try:
            return original(spec,upstream)
        except ValueError as exc:
            if str(exc)!='cached environment has no portable pip dependency list' or not all(
                    s in spec.get('packages','') for s in ('numpy==1.19.2','scipy==1.5.2','cython==3.0.10')):
                raise
            return mixed_recipe(spec,upstream)
    provision.local_dependency_recipe=recipe
    tasks=json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text())
    qualified=[];deferred=[]
    for task in tasks:
        instance=task['instance_id']
        work=ROOT/'qualification'/instance
        receipt=work/'qualification.json'
        if receipt.exists():
            old=json.loads(receipt.read_text())
            if old['status']=='PASS':
                qualified.append(instance)
            else:
                deferred.append({'task':instance,'reason':old.get('reason'),'attempted_again':False})
            continue
        if work.exists():
            # A previous partial setup is not silently retried either. Surface a
            # terminal no-model preparation receipt so the queue can skip it.
            provision.record(receipt,{'status':'BLOCKED','instance_id':instance,
                'reason':'prior incomplete preparation preserved; separate retry required',
                'model_requests':0,'paper_data':False})
            deferred.append({'task':instance,'reason':'prior incomplete preparation'})
            continue
        try:
            provision.prepare_one(task)
            qualified.append(instance)
        except Exception as exc:
            deferred.append({'task':instance,'reason':str(exc),'automatic_retry':False})
        provision.record(ROOT/'reports/preparation-skip-v5-state.json',{
            'status':'PREPARING','qualified':qualified,'deferred':deferred,
            'model_requests':0,'operator_sha256':sha(Path(__file__)),'updated':time.time()})
    result={'status':'PREPARATION_PASS_COMPLETE','registered':len(tasks),'qualified':qualified,
        'deferred':deferred,'model_requests':0,'automatic_retries':0,
        'operator_sha256':sha(Path(__file__)),'updated':time.time()}
    provision.record(ROOT/'reports/preparation-skip-v5-state.json',result)
    provision.record(ROOT/'reports/provision-state.json',result)


if __name__=='__main__':
    main()
