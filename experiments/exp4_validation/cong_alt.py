#!/usr/bin/env python3
"""CNN congestion label decomposition + alternative label (new files only).

Reads cong_decomp.json (stored per-layer capacity/usage of 5_1_grt.odb + guide-derived routed
demand) and the released npz. Writes OUT_NPZ with:
  cong_demand_over_available [2,ny,nx]  = routed demand / (capacity - pre-occupancy), per H/V
  cong_routed_demand, cong_preoccupied, cong_capacity [2,ny,nx] (tracks)
  cong_stored_check [2,ny,nx] = stored usage / capacity (must equal the released `cong`)
and prints/returns pooled stats. Usage: cong_alt.py DECOMP.json RELEASED.npz OUT.npz STATS.json"""
import json, sys
import numpy as np
dec = json.load(open(sys.argv[1])); z = np.load(sys.argv[2]); ny, nx = dec['ny'], dec['nx']
S = {k: {d: np.zeros((ny, nx)) for d in 'HV'} for k in ('cap', 'use', 'dem')}
per_layer = {}
for name, L in dec['layers'].items():
    d = 'H' if L['dir'] == 'HORIZONTAL' else 'V'
    for k in ('cap', 'use', 'dem'):
        a = np.array(L[k], float); S[k][d] += a
    per_layer[name] = dict(dir=d, cap=float(np.sum(L['cap'])), use=float(np.sum(L['use'])), dem=float(np.sum(L['dem'])))
out = {}
for d in 'HV':
    cap, use, dem = S['cap'][d], S['use'][d], S['dem'][d]
    pre = use - dem
    avail = cap - pre
    with np.errstate(divide='ignore', invalid='ignore'):
        out[d] = dict(stored=np.where(cap > 0, use / cap, 0.0), alt=np.where(avail > 0, dem / avail, 0.0), pre=pre, dem=dem, cap=cap)
stored = np.stack([out['H']['stored'], out['V']['stored']]).astype(np.float32)
alt = np.stack([out['H']['alt'], out['V']['alt']]).astype(np.float32)
np.savez_compressed(sys.argv[3], cong_demand_over_available=alt,
                    cong_routed_demand=np.stack([out['H']['dem'], out['V']['dem']]).astype(np.float32),
                    cong_preoccupied=np.stack([out['H']['pre'], out['V']['pre']]).astype(np.float32),
                    cong_capacity=np.stack([out['H']['cap'], out['V']['cap']]).astype(np.float32),
                    cong_stored_check=stored, x_edges_um=z['x_edges_um'], y_edges_um=z['y_edges_um'],
                    definition=np.array(['cong_demand_over_available = routed demand (GR guide edge crossings) / (stored capacity - (stored usage - routed demand)), per H/V; released cong = stored usage / stored capacity']))


def spearman(a, b):
    ra = np.argsort(np.argsort(a)); rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1]) if a.size > 2 and a.std() > 0 and b.std() > 0 else None


st = dict(shape=[ny, nx], released_equals_stored=bool(z['cong'].shape == stored.shape and np.abs(z['cong'] - stored).max() <= 1e-6),
          min_preoccupied=float(min(out['H']['pre'].min(), out['V']['pre'].min())), per_layer=per_layer)
for i, d in enumerate('HV'):
    use = out[d]['dem'] + out[d]['pre']
    st[d] = dict(usage_total=float(use.sum()), routed_demand_total=float(out[d]['dem'].sum()), preoccupied_total=float(out[d]['pre'].sum()),
                 preoccupied_share_of_usage=float(out[d]['pre'].sum() / use.sum()) if use.sum() else None,
                 usage_over_demand=float(use.sum() / out[d]['dem'].sum()) if out[d]['dem'].sum() else None,
                 mean_label_stored=float(stored[i].mean()), mean_label_alt=float(alt[i].mean()),
                 mean_label_pre_only=float(np.where(out[d]['cap'] > 0, out[d]['pre'] / np.maximum(out[d]['cap'], 1), 0).mean()),
                 gcell_share_of_label_from_preoccupancy=float(np.where(use > 0, out[d]['pre'] / np.maximum(use, 1e-12), 0)[use > 0].mean()) if (use > 0).any() else None,
                 spearman_stored_vs_alt=spearman(stored[i].ravel(), alt[i].ravel()),
                 spearman_stored_vs_preoccupied=spearman(stored[i].ravel(), out[d]['pre'].ravel()))
json.dump(st, open(sys.argv[4], 'w'), indent=1)
print('CONGALT', sys.argv[2].split('/')[-1][:30], 'eq', st['released_equals_stored'], 'pre-share H %.3f V %.3f' % (st['H']['preoccupied_share_of_usage'] or -1, st['V']['preoccupied_share_of_usage'] or -1))
