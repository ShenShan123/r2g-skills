"""Pre-episode dependency completion; no frozen runtime or tests are changed."""
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'adapters'))
from provision import execute,eval_worktree,record
from local_sandbox import sandbox_args


def main():
    instance='pydata__xarray-2905'
    for arm in ('native','tehm'):
        if (ROOT/'hosts/swe-agent'/arm/'episodes'/instance).exists():
            raise ValueError('environment cannot change after this task starts')
    task=next(t for t in json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text())
              if t['instance_id']==instance)
    work=ROOT/'qualification'/instance
    previous=json.loads((work/'qualification.json').read_text())
    if previous['status']!='BLOCKED':
        raise ValueError('no blocked qualification to complete')
    env=work/'setup-home/envs/testbed'
    command=('export PATH=/root/envs/testbed/bin:/usr/bin:/bin; '
             'python -m pip install --no-deps bottleneck==1.3.8 sparse==0.13.0')
    execute(sandbox_args(work/'base',work/'setup-home')+['--','/bin/bash','-ec',command],
            work/'optional-dependency-install.log',timeout=1800)
    record(work/'optional-dependency-amendment.json',{'reason':'required benchmark regression tests skipped for missing optional packages',
        'optional_packages':['bottleneck==1.3.8','sparse==0.13.0'],
        'model_requests':0,'task_source_or_test_changes':False,'both_arms_same_environment':True,
        'operator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'created':time.time()})
    baseline=eval_worktree(task,work/'base',work/'setup-home',env,work/'baseline-eval-deps-r2','')
    reference=eval_worktree(task,work/'base',work/'setup-home',env,work/'reference-eval-deps-r2',task['patch'])
    (work/'qualification.json').rename(work/'qualification-before-optional-dependencies.json')
    success=baseline['status']=='FAIL' and reference['status']=='PASS'
    result={'status':'PASS' if success else 'BLOCKED','instance_id':instance,'base_commit':task['base_commit'],
        'baseline_status':baseline['status'],'reference_status':reference['status'],
        'environment_prefix':str(env),'repo':str(work/'base'),
        'baseline_grade':str(work/'baseline-eval-deps-r2/grade.json'),
        'reference_grade':str(work/'reference-eval-deps-r2/grade.json'),
        'previous_receipt':'qualification-before-optional-dependencies.json',
        'model_requests':0,'paper_data':False,'reason':None if success else 'local qualification still incomplete'}
    record(work/'qualification.json',result)
    if not success:
        raise RuntimeError('qualification remains blocked; no task replacement or paid execution')
    # Existing, frozen provisioner continues only the same fixed roster.
    import provision
    sys.argv=[str(ROOT/'adapters/provision.py')]
    provision.main()


if __name__=='__main__':
    main()
