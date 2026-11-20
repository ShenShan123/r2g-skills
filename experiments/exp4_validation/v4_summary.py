#!/usr/bin/env python3
"""V4 summary: per-defect detection rate (V1, dataset-own, images) relative to clean copies.

Protocol rule: detected iff >=1 check that PASSES on the clean copy FAILS on the corrupted copy.
Secondary (reported separately, not the protocol rule): also count a check that already fails on
clean but matches strictly fewer entities on the corrupted copy.
"""
import glob, json, sys
from collections import defaultdict
from pathlib import Path

W = Path(sys.argv[1])


def load_v1(f):
    r = json.load(open(f))
    return {k: (bool(v.get('passed')), v.get('matched')) for k, v in r['groups'].items() if v.get('applicable', True)}


def load_own(f):
    return {k: (v['passed'], None) for k, v in json.load(open(f))['checks'].items()}


def load_img(f):
    r = json.load(open(f))
    return {k: (bool(v.get('passed')), v.get('matched')) for k, v in r.items() if isinstance(v, dict) and k != 'verdict' and v.get('applicable', True)}


def compare(clean, bad):
    new = sorted(k for k, (p, _) in bad.items() if clean.get(k, (True, None))[0] and not p)
    worse = sorted(k for k, (p, m) in bad.items() if k in clean and not clean[k][0] and not p and m is not None
                   and clean[k][1] is not None and m < clean[k][1])
    return new, worse


import hashlib
manifest = json.load(open('/data/r2g_n45_pilot500/R2G_dataset_6labels_v1/analysis/dataset_manifest.json'))['graph_sha256']
effective = {}
for pj in glob.glob(str(W / 'v4/copies/*/*/paths.json')):
    defect, design = Path(pj).parts[-3], Path(pj).parts[-2]
    paths = json.load(open(pj))
    effective[(defect, design)] = sum(hashlib.sha256(Path(p).read_bytes()).hexdigest() != manifest[f'{k}/{design}.pt'] for k, p in paths.items())


def load_own_content(f):
    return {k: v for k, v in load_own(f).items() if k != 'O1_manifest_sha256'}


out = {}
for kind, pattern, loader in (('graph_v1', 'v4/results/*__*.v1.json', load_v1), ('graph_own', 'v4/results/*__*.own.json', load_own), ('graph_own_content_only', 'v4/results/*__*.own.json', load_own_content),
                              ('image_v1', 'v4img/results/*__*.json', load_img)):
    files = sorted(glob.glob(str(W / pattern)))
    by = defaultdict(dict)
    for f in files:
        name = Path(f).name.replace('.v1.json', '').replace('.own.json', '') if kind != 'image_v1' else Path(f).name[:-5]
        defect, design = name.split('__', 1)
        by[design][defect] = loader(f)
    table = defaultdict(lambda: dict(n=0, detected=0, detected_incl_worsening=0, examples={}))
    clean_fail = {}
    for design, m in by.items():
        clean = m.get('clean', {})
        clean_fail[design] = sorted(k for k, (p, _) in clean.items() if not p)
        for defect, res in m.items():
            if defect == 'clean':
                continue
            new, worse = compare(clean, res)
            if kind.startswith('graph') and effective.get((defect, design)) == 0:
                table[defect].setdefault('no_op_injections', []).append(design)
                continue  # the defect did not change this design's graphs (e.g. WNS already <= 0)
            t = table[defect]; t['n'] += 1; t['detected'] += bool(new); t['detected_incl_worsening'] += bool(new or worse)
            t['examples'][design] = dict(new_failures=new[:8], worsened=worse[:5])
    for t in table.values():
        t['rate'] = t['detected'] / t['n'] if t['n'] else None
        t['rate_incl_worsening'] = t['detected_incl_worsening'] / t['n'] if t['n'] else None
        t['per_check'] = {}
    for design, m in by.items():
        clean = m.get('clean', {})
        for defect, res in m.items():
            if defect == 'clean' or defect not in table or (kind.startswith('graph') and effective.get((defect, design)) == 0):
                continue
            for k in compare(clean, res)[0]:
                table[defect]['per_check'][k] = table[defect]['per_check'].get(k, 0) + 1
    out[kind] = dict(detection=dict(sorted(table.items())), clean_copy_failures=clean_fail,
                     clean_copies=len(clean_fail), clean_copies_failing=sum(bool(v) for v in clean_fail.values()))
(W / 'v4_summary.json').write_text(json.dumps(out, indent=1))
for kind, o in out.items():
    print('==', kind, 'clean copies failing:', o['clean_copies_failing'], '/', o['clean_copies'])
    for d, t in o['detection'].items():
        print(f"  {d:42s} {t['detected']}/{t['n']}  (incl. worsening {t['detected_incl_worsening']}/{t['n']}) no-op={t.get('no_op_injections', [])} checks={sorted(t['per_check'])[:6] if kind.startswith('graph_own') else len(t['per_check'])}")
