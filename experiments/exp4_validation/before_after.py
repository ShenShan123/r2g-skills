#!/usr/bin/env python3
"""Before/after V1 per-group failure counts (v1/ vs v1a/, img_v1/ vs img_v1a/) -> v1_before_after.json"""
import glob, json, sys
from collections import Counter
from pathlib import Path
W = Path(sys.argv[1]); out = {}
for kind, a, b, key in (('graph', 'v1', 'v1a', 'groups'), ('image', 'img_v1', 'img_v1a', None)):
    res = {}
    for tag, d in (('before', a), ('after', b)):
        fails = Counter(); designs = Counter(); passed = 0
        for f in glob.glob(str(W / d / '*.json')):
            r = json.load(open(f)); g = r[key] if key else {k: v for k, v in r.items() if isinstance(v, dict) and k != 'verdict'}
            ok = True
            for k, v in g.items():
                if not v.get('applicable', True):
                    continue
                designs[k] += 1
                if not v.get('passed', True):
                    fails[k] += 1; ok = False
            passed += ok
        res[tag] = dict(designs=len(glob.glob(str(W / d / '*.json'))), designs_all_pass=passed, failures=dict(fails), applicable=dict(designs))
    keys = sorted(set(res['before']['failures']) | set(res['after']['failures']))
    res['table'] = {k: [res['before']['failures'].get(k, 0), res['after']['failures'].get(k, 0)] for k in keys}
    out[kind] = res
(W / 'v1_before_after.json').write_text(json.dumps(out, indent=1))
for kind, r in out.items():
    print(kind, 'designs all-pass before', r['before']['designs_all_pass'], '/', r['before']['designs'], 'after', r['after']['designs_all_pass'], '/', r['after']['designs'])
    for k, (x, y) in r['table'].items():
        print(f'  {k:42s} {x:3d} -> {y:3d}')
