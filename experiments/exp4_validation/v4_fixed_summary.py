#!/usr/bin/env python3
"""V4b: negative controls on FIXED-builder copies (a clean baseline on which V1 passes fully)."""
import glob, hashlib, json, sys
from collections import defaultdict
from pathlib import Path
W = Path(sys.argv[1]); out = {}


def groups(f, img):
    r = json.load(open(f)); g = r if img else r['groups']
    return {k: bool(v.get('passed')) for k, v in g.items() if isinstance(v, dict) and k != 'verdict' and v.get('applicable', True)}


for kind, pat, img in (('graph', 'v4fixed/results/*.v1.json', False), ('image', 'v4imgfixed/results/*.json', True)):
    by = defaultdict(dict)
    for f in glob.glob(str(W / pat)):
        n = Path(f).name.replace('.v1.json', '')[:-5 if img else None] if img else Path(f).name.replace('.v1.json', '')
        if img:
            n = Path(f).name[:-5]
        d, x = n.split('__', 1); by[x][d] = groups(f, img)
    tab = defaultdict(lambda: dict(n=0, detected=0, no_op=[]))
    clean_fail = {}
    for x, m in by.items():
        clean = m['clean']; clean_fail[x] = sorted(k for k, p in clean.items() if not p)
        for d, res in m.items():
            if d == 'clean':
                continue
            if not img:
                paths = json.load(open(W / f'v4fixed/copies/{d}/{x}/paths.json'))
                eff = sum(hashlib.sha256(Path(p).read_bytes()).hexdigest() != hashlib.sha256((W / 'fixdemo/root2' / k.split('/')[0] / k.split('/')[1] / f'{x}.pt').read_bytes()).hexdigest() for k, p in paths.items())
                if eff == 0:
                    tab[d]['no_op'].append(x); continue
            new = [k for k, p in res.items() if clean.get(k, True) and not p]
            tab[d]['n'] += 1; tab[d]['detected'] += bool(new)
    out[kind] = dict(clean_copies=len(clean_fail), clean_copies_failing=sum(bool(v) for v in clean_fail.values()),
                     clean_failures=clean_fail, detection=dict(sorted(tab.items())))
(W / 'v4_fixed_baseline_summary.json').write_text(json.dumps(out, indent=1))
for kind, o in out.items():
    print(kind, 'clean failing', o['clean_copies_failing'], '/', o['clean_copies'])
    for d, t in o['detection'].items():
        print(f"  {d:42s} {t['detected']}/{t['n']} no-op={len(t['no_op'])}")
