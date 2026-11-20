#!/usr/bin/env python3
"""Aggregate V1 per-design JSONs (+ OpenSTA reruns, image checks) into v1_summary.json."""
import glob, json, math, re, sys
from collections import defaultdict
from pathlib import Path

W = Path(sys.argv[1])
V1DIR = sys.argv[2] if len(sys.argv) > 2 else 'v1'
IMGDIR = sys.argv[3] if len(sys.argv) > 3 else 'img_v1'
OUTF = sys.argv[4] if len(sys.argv) > 4 else 'v1_summary.json'
sample = json.load(open(W / 'code/sample.json'))
stratum = {d['design']: k for k, v in sample['v1'].items() for d in v}
agg = defaultdict(lambda: dict(designs=0, designs_passed=0, n=0, matched=0, max_abs=0.0, max_rel=0.0, failed_designs=[]))
per_design = {}
for f in sorted(glob.glob(str(W / V1DIR / '*.json'))):
    r = json.load(open(f)); d = r['design']
    per_design[d] = dict(stratum=stratum.get(d), passed=r['verdict']['passed'], failed=r['verdict']['failed_groups'])
    for k, v in r['groups'].items():
        if not v.get('applicable', True):
            continue
        a = agg[k]; a['designs'] += 1; a['designs_passed'] += bool(v.get('passed'))
        if not v.get('passed'):
            a['failed_designs'].append(d)
        n = v.get('n', v.get('n_truth')); m = v.get('matched')
        if n is not None and m is not None:
            a['n'] += n; a['matched'] += m
        for key in ('max_abs', 'max_rel'):
            if v.get(key) is not None:
                a[key] = max(a[key], v[key])
        if 'f1' in v:
            a.setdefault('min_f1', 1.0); a['min_f1'] = min(a['min_f1'], v['f1'])
for a in agg.values():
    a['entity_match_rate'] = a['matched'] / a['n'] if a['n'] else None
# STA rerun
sta = {}
for f in sorted(glob.glob(str(W / 'sta/*.txt'))):
    d = Path(f).stem
    t = open(f).read()
    ws = re.search(r'R2G_WS\s+(\S+)', t); tn = re.search(r'R2G_TNS\s+(\S+)', t)
    gt = json.load(open(W / V1DIR / f'{d}.json'))
    js = list((Path('/data/r2g_n45_pilot500/nangate45_pilot500') / d / 'logs').glob('**/6_report.json'))
    rep = json.load(open(js[0])) if js else {}
    res = json.load(open(Path('/data/r2g_n45_pilot500/nangate45_pilot500') / d / 'result.json'))
    def num(m):
        try:
            return float(m.group(1)) if m else None
        except ValueError:
            return None
    a_ws, a_tns = num(ws), num(tn)
    j_ws, j_tns = rep.get('finish__timing__setup__ws'), rep.get('finish__timing__setup__tns')
    cons = res.get('timing_path_constrained') is True
    row = dict(rerun_ws=a_ws, rerun_tns=a_tns, json_ws=j_ws, json_tns=j_tns, constrained=cons)
    if cons and a_ws is not None and j_ws is not None:
        row['d_ws'] = a_ws - j_ws; row['d_tns'] = a_tns - j_tns
        row['agree'] = abs(row['d_ws']) <= 0.005 and abs(row['d_tns']) <= max(0.01, 0.01 * abs(j_tns))
    sta[d] = row
cons_rows = [r for r in sta.values() if r['constrained'] and 'agree' in r]
sta_sum = dict(designs=len(sta), constrained=len(cons_rows), agree=sum(r['agree'] for r in cons_rows),
               max_abs_d_ws=max((abs(r['d_ws']) for r in cons_rows), default=None),
               max_abs_d_tns=max((abs(r['d_tns']) for r in cons_rows), default=None),
               unconstrained=[d for d, r in sta.items() if not r['constrained']])
# images
img = defaultdict(lambda: dict(designs=0, passed=0, failed_designs=[], max_abs=0.0))
img_diag = {}
for f in sorted(glob.glob(str(W / IMGDIR / '*.json'))):
    d = Path(f).stem; r = json.load(open(f))
    for k, v in r.items():
        if k == 'verdict' or not isinstance(v, dict):
            continue
        if not v.get('applicable', True):
            img_diag.setdefault(k, {})[d] = {kk: vv for kk, vv in v.items() if kk in ('passed', 'max_abs', 'dropped_fraction', 'truth', 'claim', 'resource_equal', 'demand_equal')}
            continue
        a = img[k]; a['designs'] += 1; a['passed'] += bool(v.get('passed'))
        if not v.get('passed'):
            a['failed_designs'].append(d)
        if v.get('max_abs') is not None:
            a['max_abs'] = max(a['max_abs'], v['max_abs'])
out = dict(n_designs=len(per_design), designs_passed=sum(v['passed'] for v in per_design.values()),
           groups=dict(sorted(agg.items())), sta_rerun=dict(summary=sta_sum, per_design=sta),
           images=dict(groups=dict(img), diagnostics=img_diag), per_design=per_design)
Path(W / OUTF).write_text(json.dumps(out, indent=1, default=float))
print(json.dumps(dict(n=out['n_designs'], passed=out['designs_passed'], sta=sta_sum), indent=1))
for k, a in sorted(agg.items()):
    print(f"{k:45s} {a['designs_passed']:3d}/{a['designs']:3d}  rate={a['entity_match_rate'] if a['entity_match_rate'] is None else round(a['entity_match_rate'], 6)}  max_abs={a['max_abs']:.3g}")
for k, a in img.items():
    print('IMG', k, a['passed'], '/', a['designs'], a['max_abs'])
