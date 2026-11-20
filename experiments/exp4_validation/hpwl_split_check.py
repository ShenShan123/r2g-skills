#!/usr/bin/env python3
"""Route-stage net HPWL on SPLIT nets: dataset value vs Σ over the net's CTS-stage physical pieces
of each piece's own HPWL (documented rule in 02_extract_features: per-subnet HPWL summed; bbox =
union). Truth pieces come from our independent lineage (lineage_check / gt_raw.buffer_groups).
Usage: hpwl_split_check.py DESIGN_DIR GT.pkl DATASET_ROOT OUT.json"""
import json, pickle, sys
from collections import defaultdict
from pathlib import Path
import numpy as np, torch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gt_raw import buffer_groups, orient_point  # noqa
from adapter_r2g6 import NAME_CONVENTION as F  # noqa
ddir, gtp, droot, outp = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
gt = pickle.load(open(gtp, 'rb')); lib = gt['lib']; mac = gt['lef']['macros']; d = ddir.name
fp = gt['stages']['2_floorplan']; ct = gt['stages']['4_cts']; dbu = ct['dbu']
sig = {n: r for n, r in fp['nets'].items() if r['use'] not in ('POWER', 'GROUND')}
fc = defaultdict(dict)
for n, r in sig.items():
    for i, p in r['conns']:
        fc[i][p] = n
fvl = dict(insts={i: dict(master=fp['comps'][i]['master'], conns=c) for i, c in fc.items() if fp['comps'][i]['master'] in lib}, aliases=[],
           ports={io: v['dir'] for io, v in fp['iopins'].items() if v['use'] not in ('POWER', 'GROUND')})
nets = {n: r for n, r in ct['nets'].items() if r['use'] not in ('POWER', 'GROUND')}
bg = buffer_groups(fvl, lib, nets, ct['comps'], merge_buffers=False)
pieces = defaultdict(list)
for n in nets:
    o = bg['route_owner'].get(n)
    if o:
        pieces[o].append(n)


def pos(i, p):
    c = ct['comps'].get(i); m = mac.get(c['master']) if c else None
    if not c or c['x'] is None or not m or p not in m['pins'] or not m['pins'][p]['bbox']:
        return None
    b = m['pins'][p]['bbox']; x, y = orient_point((b[0] + b[2]) / 2, (b[1] + b[3]) / 2, m['w'], m['h'], c['orient'])
    return c['x'] / dbu + x, c['y'] / dbu + y


def hp(n):
    pts = [pos(i, p) for i, p in nets[n]['conns']] + [((ct['iopins'][io]['x'] / dbu, ct['iopins'][io]['y'] / dbu) if ct['iopins'].get(io, {}).get('x') is not None else None) for io in nets[n]['ios']]
    if any(q is None for q in pts) or len(pts) < 1:
        return None
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return max(xs) - min(xs) + max(ys) - min(ys)


res = {}
for tag, root in (('release', droot), ('fixed', Path(sys.argv[5]) if len(sys.argv) > 5 else None)):
    if root is None or not (root / 'route/B' / f'{d}.pt').exists():
        continue
    g = torch.load(root / 'route/B' / f'{d}.pt', map_location='cpu', weights_only=False)
    sch = list(g['net'].x_schema); x = g['net'].x.double()
    a = dict(zip(g['net'].names, x[:, sch.index('hpwl_um')].tolist()))
    n_ok = n = 0; worst = []
    for c, ps in pieces.items():
        if len(ps) < 2 or F(c) not in a or a[F(c)] != a[F(c)]:
            continue
        v = [hp(q) for q in ps]
        if any(t is None for t in v):
            continue
        t = sum(v); n += 1
        ok = abs(a[F(c)] - t) <= 1e-3 + 1e-5 * abs(t); n_ok += ok
        if not ok and len(worst) < 3:
            worst.append(dict(net=c, pieces=len(ps), dataset=a[F(c)], truth=t))
    res[tag] = dict(split_nets_compared=n, matched=n_ok, examples=worst)
outp.write_text(json.dumps(dict(design=d, results=res), indent=1)); print('HPWLSPLIT', d, res)
