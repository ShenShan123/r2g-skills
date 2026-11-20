#!/usr/bin/env python3
"""Aggregate def-graph check results for one tag: per-group pass counts + lineage + timings."""
import glob, json, sys
from collections import Counter, defaultdict
from pathlib import Path
W, TAG = Path(sys.argv[1]), sys.argv[2]
out = dict(tag=TAG, products={}, timing={}, lineage=Counter(), clock_nets=[], failures=defaultdict(list))
for kind, pat in (('b_f', '*.bf.json'), ('four_stage', '*.4stage.json'), ('images', '*.img.json')):
    agg = defaultdict(lambda: [0, 0, 0, 0])   # designs passed, designs, entities matched, entities
    for f in sorted(glob.glob(str(W / 'dgv1' / TAG / pat))):
        r = json.load(open(f)); g = r.get('groups', r); d = Path(f).name.split('.')[0]
        for k, v in g.items():
            if not isinstance(v, dict) or k == 'verdict' or not v.get('applicable', True):
                continue
            a = agg[k]; a[1] += 1; a[0] += bool(v.get('passed'))
            if v.get('n', v.get('n_truth')) is not None and v.get('matched') is not None:
                a[2] += v['matched']; a[3] += v.get('n', v.get('n_truth'))
            if not v.get('passed'):
                out['failures'][f'{kind}:{k}'].append(d)
        if kind == 'four_stage':
            out['lineage'].update(g['N_lineage_categories']['categories'])
            out['clock_nets'] += [dict(design=d, **c) for c in g.get('N_clock_nets', {}).get('nets', [])]
    out['products'][kind] = {k: dict(designs_passed=v[0], designs=v[1], matched=v[2], entities=v[3]) for k, v in sorted(agg.items())}
for f in sorted(glob.glob(str(W / 'defgraph/projects' / TAG / '*.timing.tsv'))):
    d = Path(f).name.split('.')[0]
    out['timing'][d] = {l.split('\t')[0]: [int(l.split('\t')[1]), float(l.split('\t')[2])] for l in open(f) if l.strip()}
out['lineage'] = dict(out['lineage']); out['failures'] = dict(out['failures'])
(W / 'dgv1' / TAG / 'summary.json').write_text(json.dumps(out, indent=1))
print(json.dumps(dict(failures=out['failures'], lineage=out['lineage']), indent=1))
for kind, groups in out['products'].items():
    bad = {k: v for k, v in groups.items() if v['designs_passed'] != v['designs']}
    print(kind, len(groups), 'groups;', 'all pass' if not bad else bad)
