"""Apply explicit, evidenced family merges to a separate review map, not a live queue."""
import argparse
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def apply_decisions(rows, decisions):
    parents = {r['family_id']: r['family_id'] for r in rows}
    def root(key):
        while parents[key] != key:
            key = parents[key]
        return key
    for decision in decisions:
        keys = decision['families']
        if len(set(keys)) < 2 or any(k not in parents for k in keys):
            raise ValueError('Merge needs at least two known family groups')
        if not decision.get('reason') or not decision.get('evidence'):
            raise ValueError('Merge requires a reason and evidence')
        canonical = min(root(k) for k in keys)
        for key in keys:
            parents[root(key)] = canonical
    return {r['task_id']: root(r['family_id']) for r in rows}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--decisions', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Do not replace prior family review evidence')
    rows = json.loads(args.plan.read_text())['rows']
    decisions = json.loads(args.decisions.read_text())['decisions']
    mapping = apply_decisions(rows, decisions)
    evidence = {path: digest(path) for d in decisions for path in d['evidence']}
    result = {'family_reviewed': False, 'split_assigned': False,
              'plan_sha256': digest(args.plan), 'decisions_sha256': digest(args.decisions),
              'before_families': len({r['family_id'] for r in rows}),
              'after_families': len(set(mapping.values())),
              'design_to_family': mapping, 'evidence_sha256': evidence,
              'note': 'Partial review only. Apply this map before a final group split; live export plan unchanged.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k: result[k] for k in ('before_families', 'after_families', 'family_reviewed')}))


if __name__ == '__main__':
    main()
