"""Pre-model compatibility restriction for the native v1.1.0 file tools.

The pinned tree-sitter tool requires Python >=3.8. This decision uses upstream
task-version specs only, never qualification success, repairs or arm outcomes.
The initial unqualified roster is retained, not deleted or relabelled.
"""
import collections
import hashlib
import json
from pathlib import Path
import pyarrow.parquet as pq

from select_tasks import ROOT, PUBLIC, select, save
from official import spec_for


def main():
    if (ROOT/'inputs/registration.json').exists():
        raise ValueError('formal roster frozen; no reselection')
    if list((ROOT/'hosts').glob('**/provider/requests.jsonl')):
        raise ValueError('model requests already exist; no reselection')
    previous=ROOT/'inputs/initial-unqualified-roster'
    previous.mkdir(exist_ok=False)
    for name in ('public-tasks.json','selection-receipt.json','dataset-pin-verification.json'):
        (ROOT/'inputs'/name).rename(previous/name)
    (ROOT/'inputs/evaluator-private/tasks.json').rename(previous/'private-tasks.json')
    (previous/'private-tasks.json').chmod(0o600)
    rows=[]
    for path in sorted((ROOT/'inputs/evaluator-private').glob('*.parquet')):
        rows.extend(pq.read_table(path).to_pylist())
    eligible,excluded=[],[]
    for row in rows:
        try:
            version=spec_for(row)['python']
            compatible=(3,8)<=tuple(map(int,version.split('.'))) <= (3,11)
        except (KeyError,ValueError):
            compatible=False;version='UNKNOWN'
        if compatible:
            eligible.append(row)
        else:
            excluded.append({'instance_id':row['instance_id'],'python':version,
                             'reason':'native pinned file-tool Python compatibility'})
    chosen=select(eligible)
    original=json.loads((previous/'public-tasks.json').read_text())
    revised={**original,'public_tasks':[{k:r[k] for k in sorted(PUBLIC)} for r in chosen],
             'eligibility':'pinned upstream Python minor 3.8 through 3.11; native file-tool compatibility; pre-model decision'}
    private=ROOT/'inputs/evaluator-private/tasks.json'
    save(private,chosen);private.chmod(0o600)
    save(ROOT/'inputs/public-tasks.json',revised)
    save(ROOT/'inputs/eligibility-receipt.json',{'eligible':len(eligible),'excluded':excluded,
        'rule':revised['eligibility'],'no_arm_or_repair_outcomes_used':True,
        'previous_roster_retained':str(previous),'selection_seed':original['seed']})
    save(ROOT/'inputs/selection-receipt.json',{'registered_tasks':20,'episodes':40,
        'by_repository':dict(collections.Counter(r['repo'] for r in chosen)),
        'dataset_revision':original['revision'],
        'private_inputs_sha256':hashlib.sha256(private.read_bytes()).hexdigest(),
        'public_inputs_sha256':hashlib.sha256((ROOT/'inputs/public-tasks.json').read_bytes()).hexdigest(),
        'eligibility':revised['eligibility']})
    pin=json.loads((previous/'dataset-pin-verification.json').read_text())
    save(ROOT/'inputs/dataset-pin-verification.json',{**pin,'subset_eligibility_receipt':'eligibility-receipt.json',
        'all_20_private_records_identical':all(r in rows for r in chosen),
        'all_public_inputs_identical':'same pinned source fields, compatibility-filtered subset'})
    print(json.dumps(json.loads((ROOT/'inputs/selection-receipt.json').read_text()),indent=2))


if __name__=='__main__':
    main()
