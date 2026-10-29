"""Explicit zero-model requalification after the shared local runtime fix."""
import json
from select_tasks import ROOT
from provision import eval_worktree,record


def main():
    instance='sphinx-doc__sphinx-9673'
    if list((ROOT/'hosts').glob('**/provider/requests.jsonl')):
        raise ValueError('no qualification changes after model execution')
    task=next(t for t in json.loads((ROOT/'inputs/evaluator-private/tasks.json').read_text())
              if t['instance_id']==instance)
    work=ROOT/'qualification'/instance
    previous=json.loads((work/'qualification.json').read_text())
    if previous['status']=='PASS':
        raise ValueError('qualification already passed')
    env=work/'setup-home/envs/testbed'
    baseline=eval_worktree(task,work/'base',work/'setup-home',env,work/'baseline-eval-r3','')
    reference=eval_worktree(task,work/'base',work/'setup-home',env,work/'reference-eval-r3',task['patch'])
    (work/'qualification.json').rename(work/'qualification-before-runtime-pythonpath.json')
    result={**previous,'status':'PASS' if baseline['status']=='FAIL' and reference['status']=='PASS' else 'BLOCKED',
            'baseline_status':baseline['status'],'reference_status':reference['status'],
            'previous_receipt':'qualification-before-runtime-pythonpath.json',
            'reason':None if baseline['status']=='FAIL' and reference['status']=='PASS' else 'local tests unqualified'}
    record(work/'qualification.json',result)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()
