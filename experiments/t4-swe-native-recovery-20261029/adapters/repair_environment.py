"""Explicit pre-model repair of a venv relocation, not an episode rerun.

tox-current-env resolves interpreter symlinks. A symlink to managed base Python
loses the task venv's packages. Match normal conda executable layout by copying
the identical executable into the private venv. Tests and candidate patches are
unchanged, previous failed qualification receipts are retained.
"""
import hashlib
import json
import shutil
from pathlib import Path
from select_tasks import ROOT
from provision import eval_worktree,record


def main():
    instance='sphinx-doc__sphinx-9673'
    if list((ROOT/'hosts').glob('**/provider/requests.jsonl')):
        raise ValueError('pre-model environment repair only')
    task=next(t for t in json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text())
              if t['instance_id']==instance)
    work=ROOT/'qualification'/instance
    old=json.loads((work/'qualification.json').read_text())
    if old['status']!='BLOCKED':
        raise ValueError('no blocked pre-model qualification to repair')
    env=work/'setup-home/envs/testbed'
    binary=env/'bin/python3.9'
    if not binary.is_symlink():
        raise ValueError('expected private venv symlink')
    target=Path(binary.readlink())
    if not target.is_absolute() or not str(target).startswith('/managed-python/'):
        raise ValueError('unexpected interpreter target; refuse')
    source=ROOT/'python-runtimes'/target.relative_to('/managed-python')
    if not source.is_file() or not source.resolve().is_relative_to((ROOT/'python-runtimes').resolve()):
        raise ValueError('managed interpreter missing or escapes experiment')
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    # Package installation/relocation, restricted to this disposable venv.
    binary.unlink()
    shutil.copy2(source,binary)
    record(work/'venv-relocation-repair.json',{'scope':'pre-model environment preparation',
        'python_binary_sha256':before,'source':str(source),'target':str(binary),
        'reason':'tox-current-env follows symlinks to base interpreter; preserve private prefix with copies',
        'task_or_test_changes':False,'model_requests':0})
    base=eval_worktree(task,work/'base',work/'setup-home',env,work/'baseline-eval-r2','')
    gold=eval_worktree(task,work/'base',work/'setup-home',env,work/'reference-eval-r2',task['patch'])
    (work/'qualification.json').rename(work/'qualification-before-venv-relocation.json')
    record(work/'qualification.json',{'status':'PASS' if base['status']=='FAIL' and gold['status']=='PASS' else 'BLOCKED',
        'instance_id':instance,'base_commit':task['base_commit'],'baseline_status':base['status'],
        'reference_status':gold['status'],'environment_prefix':str(env),'repo':str(work/'base'),
        'reason':None if base['status']=='FAIL' and gold['status']=='PASS' else 'local test qualification still incomplete',
        'previous_receipt':'qualification-before-venv-relocation.json','model_requests':0,'paper_data':False})
    print(json.dumps(json.loads((work/'qualification.json').read_text()),indent=2))


if __name__=='__main__':
    main()
