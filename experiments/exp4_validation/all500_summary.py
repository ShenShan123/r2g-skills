#!/usr/bin/env python3
"""Full-corpus (500) V1 summary with strata, defect counts and new-failure-class detection."""
import glob, json, re, sys, statistics
from collections import Counter, defaultdict
from pathlib import Path
W = Path(sys.argv[1])
cells = {l.split('\t')[0]: int(l.split('\t')[1]) for l in open('/data/r2g_n45_pilot500/select_cells.tsv') if l.strip()}
strat = {d: ('small' if c <= 1000 else 'medium' if c <= 10000 else 'large') for d, c in cells.items()}
KNOWN = {'R.F2_net_hpwl_um_unsplit': 'D-A1', 'R.F2_pin_xy_bbox_centre': 'D-A1', 'R.F3_pin_inside_shape': 'D-A1',
         'R.F1_net_pin_count': 'D-A2', 'R.F1_net_num_sinks': 'D-A2', 'R.F1_net_fanout': 'D-A2', 'X_power_density': 'D-B1'}
G = defaultdict(lambda: defaultdict(lambda: [0, 0])); failures = defaultdict(list); ent = defaultdict(lambda: [0, 0])
d_a1 = dict(designs=0, pins=0, pins_outside=0, max_um=0.0); d_a2 = dict(designs=0, nets=0); s_a1 = dict(designs=0, nets=0)
missing = []
for d in cells:
    f = W / 'v1a' / f'{d}.json'
    if not f.exists():
        missing.append(d); continue
    g = json.load(open(f))['groups']
    for k, v in g.items():
        if not v.get('applicable', True):
            continue
        for s in (strat[d], 'all'):
            G[k][s][0] += bool(v.get('passed')); G[k][s][1] += 1
        if v.get('n') is not None and v.get('matched') is not None:
            ent[k][0] += v['matched']; ent[k][1] += v['n']
        if not v.get('passed'):
            failures[k].append(d)
    if not g['R.F3_pin_inside_shape']['passed']:
        d_a1['designs'] += 1
    d_a1['pins'] += g['R.F3_pin_inside_shape']['n']; d_a1['pins_outside'] += g['R.F3_pin_inside_shape']['n'] - g['R.F3_pin_inside_shape']['matched']
    d_a1['max_um'] = max(d_a1['max_um'], g['R.F2_pin_xy_bbox_centre'].get('max_abs', 0))
    if not g['R.F1_net_pin_count']['passed']:
        d_a2['designs'] += 1; d_a2['nets'] += g['R.F1_net_pin_count']['n'] - g['R.F1_net_pin_count']['matched']
    s1 = g.get('S1_name_convention_strict_DEF_names', {})
    if s1.get('nets_renamed'):
        s_a1['designs'] += 1; s_a1['nets'] += s1['nets_renamed']
img = defaultdict(lambda: defaultdict(lambda: [0, 0])); ifail = defaultdict(list); drop = []
for d in cells:
    f = W / 'img_v1a' / f'{d}.json'
    if not f.exists():
        missing.append('img:' + d); continue
    r = json.load(open(f))
    for k, v in r.items():
        if not isinstance(v, dict) or k == 'verdict' or not v.get('applicable', True):
            continue
        for s in (strat[d], 'all'):
            img[k][s][0] += bool(v.get('passed')); img[k][s][1] += 1
        if not v.get('passed'):
            ifail[k].append(d)
    drop.append(r['diag_power_total_W']['dropped_fraction'] or 0.0)
sta = dict(designs=0, constrained=0, agree=0, max_dws=0.0, max_dtns=0.0)
for d in cells:
    t = W / 'sta' / f'{d}.txt'
    if not t.exists():
        continue
    s = open(t).read(); rj = json.load(open(f'/data/r2g_n45_pilot500/nangate45_pilot500/{d}/result.json'))
    js = json.load(open(next(Path(f'/data/r2g_n45_pilot500/nangate45_pilot500/{d}/logs').glob('**/6_report.json'))))
    sta['designs'] += 1
    m1, m2 = re.search(r'R2G_WS\s+(\S+)', s), re.search(r'R2G_TNS\s+(\S+)', s)
    if rj.get('timing_path_constrained') and m1 and m2:
        try:
            dw = float(m1.group(1)) - js['finish__timing__setup__ws']; dt = float(m2.group(1)) - js['finish__timing__setup__tns']
        except ValueError:
            continue
        sta['constrained'] += 1; sta['agree'] += abs(dw) <= 0.005 and abs(dt) <= max(0.01, 0.01 * abs(js['finish__timing__setup__tns']))
        sta['max_dws'] = max(sta['max_dws'], abs(dw)); sta['max_dtns'] = max(sta['max_dtns'], abs(dt))
lin = Counter(); lind = Counter(); lcts = Counter(); dbl = 0; al = Counter(); lin_n = 0
for f in glob.glob(str(W / 'lineage/*.json')):
    r = json.load(open(f)); lin_n += 1
    lin.update(r['group']['5_route']['categories']); lind.update(r['individual']['5_route_vs_net_lineage']['categories'])
    lcts.update(r['individual']['4_cts_route_graph_stage_net_count']['categories']); dbl += sum(g['physical_nets_in_two_dataset_groups'] for g in r['group'].values())
    al.update({k: int(not v) for k, v in r['alignment'].items()})
new_graph = {k: v for k, v in failures.items() if k not in KNOWN}
new_img = {k: v for k, v in ifail.items() if k not in KNOWN}
out = dict(designs=len(cells), missing=missing, strata=dict(Counter(strat.values())),
           graph_groups={k: dict(v) for k, v in sorted(G.items())}, graph_entity={k: v for k, v in ent.items()},
           image_groups={k: dict(v) for k, v in img.items()},
           defects=dict(D_A1=d_a1, D_A2=d_a2, D_B1=dict(designs=len(ifail.get('X_power_density', [])), dropped_fraction_median=statistics.median(drop) if drop else None,
                         dropped_fraction_max=max(drop) if drop else None, designs_with_any_drop=sum(x > 1e-9 for x in drop)), S_A1=s_a1),
           sta=sta, lineage=dict(designs=lin_n, group_route=dict(lin), individual_route=dict(lind), cts_feature=dict(lcts), double_claimed=dbl,
                                 alignment_failures={k: v for k, v in al.items() if v}),
           new_failure_classes=dict(graph={k: v[:10] for k, v in new_graph.items()}, image={k: v[:10] for k, v in new_img.items()}),
           failures_by_group={k: len(v) for k, v in failures.items()}, image_failures_by_group={k: len(v) for k, v in ifail.items()})
(W / 'all500_summary.json').write_text(json.dumps(out, indent=1))
print(json.dumps({k: out[k] for k in ('designs', 'strata', 'defects', 'sta', 'lineage', 'new_failure_classes', 'failures_by_group', 'image_failures_by_group')}, indent=1)[:6000])
print('missing', len(missing))
