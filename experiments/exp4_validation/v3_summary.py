#!/usr/bin/env python3
"""V3 summary: input identity + label change per counterfactual; dynamic read sets from strace."""
import json, re, sys
from collections import defaultdict
from pathlib import Path

import torch

W = Path(sys.argv[1])
designs = json.load(open(W / 'code/sample.json'))['v3']
ROUTE_PAT = re.compile(r'(5_route\.def|5_1_grt|5_2_route|6_final|6_report|6_finish|5_global_route|5_route_drc|5_3_fill|6_1_|result\.json)')


def labels(g):
    out = dict(graph_y=[None if v != v else float(v) for v in g.y[0].tolist()],
               wl_valid=int(g.wl_group_valid_mask.sum()), wl_sum=float(g.wl_group_y[g.wl_group_valid_mask].sum()))
    if 'gate' in g.node_types:
        out['gate_valid'] = int(g['gate'].y_valid_mask[:, 0].sum())
    return out


res = {}
for var in ('perturbed', 'donor', 'perturbed_inconsistent_dr'):
    for d in designs:
        r = dict()
        rb = W / f'v3/{var}/root2/rebuild_{d}.json'
        if not rb.exists():
            continue
        j = json.load(open(rb))
        r['builder_accepted'] = 'error' not in j
        if not r['builder_accepted']:
            r['refusal'] = j['error'].strip().splitlines()[-3:]
        cmpf = W / f'v3/{var}/cmp/{d}.json'
        if cmpf.exists():
            c = json.load(open(cmpf))
            r['inputs_identical_graphs'] = c['summary']['inputs_identical']; r['graphs'] = c['summary']['graphs']
            r['byte_identical_graphs'] = c['summary']['byte_identical']
            r['label_fields_changed'] = sorted({k for g in c['graphs'].values() for k in (g.get('fields_differing') or {})})
            base = torch.load(W / f'v2/main/root2/placement/B/{d}.pt', weights_only=False)
            cf = torch.load(W / f'v3/{var}/root2/placement/B/{d}.pt', weights_only=False)
            r['labels_baseline'] = labels(base); r['labels_counterfactual'] = labels(cf)
            gy0, gy1 = base['gate'].y, cf['gate'].y
            both = torch.isfinite(gy0[:, 0]) & torch.isfinite(gy1[:, 0])
            r['gate_congestion_changed_fraction'] = float((gy0[both, 0] != gy1[both, 0]).float().mean()) if both.any() else None
            w0, w1 = base.wl_group_y[:, 0], cf.wl_group_y[:, 0]
            b2 = torch.isfinite(w0) & torch.isfinite(w1)
            r['wl_changed_fraction'] = float((w0[b2] != w1[b2]).float().mean()) if b2.any() else None
        res[f'{var}/{d}'] = r
# strace: dynamic read set of the per-stage feature extractor subprocesses
trace = {}
for f in sorted(W.glob('v3/*/strace_*.txt')):
    procs = {}; parent = {}
    opened = defaultdict(set)
    for line in open(f, errors='replace'):
        m = re.match(r'(\d+)\s+(\w+)\((.*)\)\s+=\s+(-?\d+)', line)
        if not m:
            continue
        pid, call, args, ret = m.group(1), m.group(2), m.group(3), int(m.group(4))
        if call == 'execve' and ret == 0:
            procs[pid] = args
        elif call == 'openat' and ret >= 0:
            p = re.search(r'"([^"]+)"', args)
            if p:
                opened[pid].add(p.group(1))
    stages = {}
    for pid, args in procs.items():
        m = re.search(r'"--stage", "(\w+)"', args)
        if m:
            files = sorted(x for x in opened[pid] if '/nangate45_pilot500/' in x or '/raw_' in x or x.endswith(('.def', '.lib', '.lef', '.sdc', '.json', '.log', '.rpt', '.v')))
            design_files = sorted(x for x in files if '/raw_' in x or '/nangate45_pilot500/' in x)
            stages[m.group(1)] = dict(pid=pid, design_files_read=design_files,
                                      route_stage_files_read=[x for x in design_files if ROUTE_PAT.search(Path(x).name)])
    all_design = sorted({x for s in opened.values() for x in s if '/raw_' in x})
    trace[f.name] = dict(feature_stages=stages, whole_build_design_files=all_design,
                         whole_build_route_files=[x for x in all_design if ROUTE_PAT.search(Path(x).name)])
STATIC = {
    'graph_placement_inputs': ['2_floorplan.def (canonical topology + stage features)', 'constraint.sdc (resolved via tclsh against floorplan ports)',
                               'config.mk', 'platform Liberty/LEF', 'encode_map.csv', '1_2_yosys.v + 2_floorplan.def (wl group readout mapping)'],
    'graph_route_inputs': ['4_cts.def (stage features; topology still from 2_floorplan.def)', 'constraint.sdc', 'config.mk', 'platform Liberty/LEF', 'encode_map.csv'],
    'graph_labels_only': ['5_route.def', 'logs/**/5_1_grt.log (GRT-0053)', 'logs/**/6_report.json', 'reports/**/6_finish.rpt', 'logs/**/5_2_route.json', 'result.json',
                          '3_place.def, 4_cts.def, 5_route.def (net lineage for group ownership)'],
    'image_inputs': ['3_place.def', 'constraint.sdc', 'platform LEF/Liberty/setRC.tcl', '5_1_grt.odb (GCell grid GEOMETRY only)'],
    'image_labels_only': ['5_1_grt.odb (stored GR capacity/usage)', '5_route.def + 6_final.spef (PDNSim IR drop)'],
    'source': 'read from extract_stage_features.py (worker config strips floorplan/cts/place/route/label keys except the stage key), r2g5.build/label_bundle, upgrade_release/graph_adapter.fixed_graph_mapping, cnn_pilot500 dump_design.py/ir_design.tcl'}
img = json.load(open(W / 'v3img/v3img_cmp.json')) if (W / 'v3img/v3img_cmp.json').exists() else {}
out = dict(results=res, dynamic_reads=trace, images=img, static_allowed_reads=STATIC)
(W / 'v3_summary.json').write_text(json.dumps(out, indent=1))
print(json.dumps({k: {kk: v.get(kk) for kk in ('builder_accepted', 'inputs_identical_graphs', 'gate_congestion_changed_fraction', 'wl_changed_fraction')} for k, v in res.items()}, indent=1))
for k, v in trace.items():
    for st, s in v['feature_stages'].items():
        print(k, st, 'design files:', [Path(x).name for x in s['design_files_read']], 'route files:', s['route_stage_files_read'])
    print('whole build route files:', [Path(x).name for x in v['whole_build_route_files']])
