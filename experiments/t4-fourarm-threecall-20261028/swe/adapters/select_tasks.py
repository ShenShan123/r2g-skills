"""Freeze a deterministic, repository-stratified 20-instance Verified subset.

Only public task identity, issue and base revision enter agent inputs. Hidden
test patches, reference fixes and expected tests stay in evaluator-private.
"""
import collections
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DATASET = 'princeton-nlp/SWE-bench_Verified'
SEED = 'table4-swe20-local-v1-20261026'
PUBLIC = {'instance_id', 'repo', 'base_commit', 'problem_statement'}


def select(rows, n=20):
    if len({r['instance_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate task identities')
    groups = collections.defaultdict(list)
    key = lambda text: hashlib.sha256((SEED+'\0'+text).encode()).hexdigest()
    for row in rows:
        groups[row['repo']].append(row)
    for group in groups.values():
        group.sort(key=lambda r: key(r['instance_id']))
    order = sorted(groups, key=key)
    selected = []
    while len(selected) < n:
        progress = False
        for repo in order:
            if groups[repo] and len(selected) < n:
                selected.append(groups[repo].pop(0))
                progress = True
        if not progress:
            raise ValueError('not enough tasks')
    return selected


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x') as out:
        json.dump(value, out, indent=2, sort_keys=True)
        out.write('\n')


def main():
    target = ROOT/'inputs/public-tasks.json'
    if target.exists():
        raise ValueError('subset already fixed; never silently reselect')
    with urllib.request.urlopen('https://huggingface.co/api/datasets/'+DATASET, timeout=90) as r:
        revision = json.load(r)['sha']
    # Use the pinned revision on the dataset-server API, and retain raw pages.
    rows = []
    for offset in range(0, 500, 100):
        params = urllib.parse.urlencode(dict(dataset=DATASET, config='default', split='test',
                                             offset=offset, length=100, revision=revision))
        with urllib.request.urlopen('https://datasets-server.huggingface.co/rows?'+params, timeout=120) as r:
            page = json.load(r)
        save(ROOT/f'inputs/evaluator-private/page-{offset:03d}.json', page)
        rows.extend(item['row'] for item in page['rows'])
    if len(rows) != 500:
        raise ValueError('expected complete 500-instance dataset before sampling')
    chosen = select(rows)
    private = ROOT/'inputs/evaluator-private/tasks.json'
    save(private, chosen)
    private.chmod(0o600)
    save(target, {'dataset':DATASET, 'revision':revision, 'seed':SEED,
                  'selection':'fixed hash order, round-robin across all repositories; no model results used',
                  'public_tasks':[{k:r[k] for k in sorted(PUBLIC)} for r in chosen]})
    save(ROOT/'inputs/selection-receipt.json', {'registered_tasks':20, 'episodes':40,
        'by_repository':dict(collections.Counter(r['repo'] for r in chosen)),
        'dataset_revision':revision, 'private_inputs_sha256':hashlib.sha256(private.read_bytes()).hexdigest(),
        'public_inputs_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'prior_two_astropy_tasks_in_subset':[r['instance_id'] for r in chosen if r['instance_id'] in
                                            {'astropy__astropy-12907','astropy__astropy-13033'}]})
    for page in (ROOT/'inputs/evaluator-private').glob('page-*.json'):
        page.chmod(0o600)
    print(json.dumps(json.loads((ROOT/'inputs/selection-receipt.json').read_text()), indent=2))


if __name__ == '__main__':
    main()
