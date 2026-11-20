#!/usr/bin/env python3
"""Pool the CNN congestion-label decomposition over all designs -> cong_summary.json."""
import glob, json, re, statistics, sys
from collections import defaultdict
from pathlib import Path
W = Path(sys.argv[1]); rows = []; lay = defaultdict(lambda: dict(dem=0.0, grt_demand=0, use=0.0, cap=0.0))
for f in sorted(glob.glob(str(W / 'cong/*/stats.json'))):
    d = Path(f).parent.name; s = json.load(open(f))
    log = next(Path(f'/data/r2g_n45_pilot500/nangate45_pilot500/{d}/logs').glob('**/5_1_grt.log')).read_text()
    fin = {m[0]: int(m[2]) for m in re.findall(r'^(metal\d+)\s+(\d+)\s+(\d+)\s+[\d.]+%', log.split('Final congestion report')[-1], re.M)}
    for n, L in s['per_layer'].items():
        lay[n]['dem'] += L['dem']; lay[n]['use'] += L['use']; lay[n]['cap'] += L['cap']; lay[n]['grt_demand'] += fin.get(n, 0)
    rows.append(dict(design=d, released_equals_stored=s['released_equals_stored'], min_pre=s['min_preoccupied'],
                     **{f'{k}_{d_}': s[d_][k] for d_ in 'HV' for k in ('preoccupied_share_of_usage', 'usage_over_demand', 'mean_label_stored', 'mean_label_alt',
                                                                         'gcell_share_of_label_from_preoccupancy', 'spearman_stored_vs_alt', 'usage_total', 'routed_demand_total', 'preoccupied_total')}))
pool = {d_: dict(preoccupied_share_of_usage=sum(r[f'preoccupied_total_{d_}'] for r in rows) / sum(r[f'usage_total_{d_}'] for r in rows),
                 usage_over_demand=sum(r[f'usage_total_{d_}'] for r in rows) / sum(r[f'routed_demand_total_{d_}'] for r in rows)) for d_ in 'HV'}
dist = {k: dict(median=statistics.median(v), min=min(v), max=max(v)) for k in rows[0] if k not in ('design', 'released_equals_stored')
        for v in [[r[k] for r in rows if r[k] is not None]] if v}
for n, L in lay.items():
    L['guide_demand_over_grt_demand'] = L['dem'] / L['grt_demand'] if L['grt_demand'] else None
    L['usage_over_grt_demand'] = L['use'] / L['grt_demand'] if L['grt_demand'] else None
out = dict(designs=len(rows), released_label_equals_stored_usage_over_capacity=sum(r['released_equals_stored'] for r in rows),
           negative_preoccupancy_designs=sum(r['min_pre'] < 0 for r in rows), pooled=pool, per_design_distribution=dist, per_layer_pooled=dict(lay), per_design=rows)
(W / 'cong_summary.json').write_text(json.dumps(out, indent=1))
print(json.dumps({k: out[k] for k in ('designs', 'released_label_equals_stored_usage_over_capacity', 'negative_preoccupancy_designs', 'pooled', 'per_layer_pooled')}, indent=1))
for k in ('preoccupied_share_of_usage_H', 'preoccupied_share_of_usage_V', 'usage_over_demand_H', 'mean_label_stored_H', 'mean_label_alt_H', 'spearman_stored_vs_alt_H', 'spearman_stored_vs_alt_V'):
    print(k, dist.get(k))
