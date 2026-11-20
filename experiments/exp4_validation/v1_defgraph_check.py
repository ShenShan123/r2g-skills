#!/usr/bin/env python3
"""V1-style check of a def-graph-format b-f dataset against truth_defgraph.py ground truth.

Usage: v1_defgraph_check.py TRUTH.pkl DATASET_DIR OUT.json
Tolerances: the conversion study's frozen table (exp4_conversion/spec/CONTRACT.md §9), shared by every
arm; pass iff every entity of a group matches (match.value_compare / set_compare).
"""
import json, math, pickle, sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match import set_compare, value_compare, summarize  # noqa: E402
from adapter_defgraph import load_views  # noqa: E402

TOLS = {'coord': (1e-4, 1e-6), 'hpwl': (6e-3, 1e-6), 'lib': (1e-6, 1e-5), 'cong': (1e-6, 1e-5),
        'timing': (1e-4, 1e-6), 'wl_um': (2e-4, 1e-6), 'wl_label': (1e-6, 1e-6), 'gcap': (1e-5, 1e-5), 'exact': (0, 0)}
FEAT_TOL = dict(cell_type_id='exact', cell_area='lib', cell_power='lib', x_um='coord', y_um='coord', orientation_id='exact',
                placement_status_id='exact', net_type_id='exact', fanout='exact', pin_count='exact', num_drivers='exact',
                num_sinks='exact', connects_macro_flag='exact', num_layer='exact', hpwl_um='hpwl', pin_x_um='coord', pin_y_um='coord',
                nearest_tap_distance_um='coord', pin_direction_id='exact', pin_type_id='exact', sum_pin_cap_fF='lib')
VIEW_TYPES = dict(b=('gate', 'net', 'iopin', 'pin'), c=('gate', 'net', 'iopin'), d=('gate', 'iopin', 'pin'), e=('iopin', 'pin'), f=('gate', 'iopin'))


def main():
    T = pickle.load(open(sys.argv[1], 'rb')); V = load_views(sys.argv[2]); G = {}
    truth_nodes = dict(gate=T['gates'], net=T['nets'], iopin=T['iopins'], pin=[tuple(p) for p in T['pins']])
    tx = dict(gate=T['gate_x'], net=T['net_x'], iopin=T['iopin_x'], pin=T['pin_x'])
    pd = T['path_delay']
    for v, c in V.items():
        p = v + '.'
        for t in VIEW_TYPES[v]:
            G[p + 'I_' + t] = set_compare(c['nodes'].get(t, []), truth_nodes[t])
        extra = set(c['nodes']) - set(VIEW_TYPES[v])
        G[p + 'I_no_extra_node_types'] = dict(passed=not extra, extra=sorted(extra))
        G[p + 'T_edges_paired_fwd_rev'] = dict(passed=c['directed_pairs_ok'])
        if v == 'b':
            tru = Counter()
            for a, b in T['b_gate_pin']:
                tru[(0, frozenset([('gate', a), ('pin', tuple(b))]))] += 1
            for a, b in T['b_pin_net']:
                tru[(0, frozenset([('pin', tuple(a)), ('net', b)]))] += 1
            for a, b in T['b_iopin_net']:
                tru[(0, frozenset([('iopin', a), ('net', b)]))] += 1
        elif v == 'c':
            tru = Counter((k, frozenset([('gate' if k == 0 else 'iopin', a), ('net', b)])) for k, a, b in T['c_edges'])
        else:
            tru = Counter({(k, frozenset((t_, tuple(n_) if t_ == 'pin' else n_) for t_, n_ in e)): m for (k, e), m in T[v + '_edges'].items()})
        G[p + 'T_edges'] = set_compare(list(c['edges'].elements()), list(tru.elements()))
        for t in VIEW_TYPES[v]:
            names = c['nodes'].get(t, [])
            for col, arr in c['x'].get(t, {}).items():
                if col not in FEAT_TOL:
                    continue
                truth = [tx[t].get(n, {}).get(col, math.nan) for n in names]
                G[f'{p}F_{t}_{col}'] = value_compare(names, arr, truth, TOLS[FEAT_TOL[col]])
        # labels
        if 'gate' in c['nodes'] and 'congestion' in c['y'].get('gate', {}):
            n = c['nodes']['gate']
            G[p + 'L_congestion_raw'] = value_compare(n, c['y_raw']['gate']['congestion'], [T['congestion'][g][0] for g in n], TOLS['cong'])
            G[p + 'L_congestion'] = value_compare(n, c['y']['gate']['congestion'], [T['congestion'][g][1] for g in n], TOLS['cong'])
        if 'net' in c['nodes']:
            n = c['nodes']['net']
            G[p + 'L_wirelength_raw_um'] = value_compare(n, c['y_raw']['net']['wirelength'], [T['wirelength_um'][x] for x in n], TOLS['wl_um'])
            G[p + 'L_wirelength'] = value_compare(n, c['y']['net']['wirelength'], [math.log1p(T['wirelength_um'][x]) for x in n], TOLS['wl_label'])
            G[p + 'L_ground_cap_raw_fF'] = value_compare(n, c['y_raw']['net']['ground_cap'], [T['ground_cap_fF'][x] for x in n], TOLS['gcap'])
            G[p + 'L_ground_cap'] = value_compare(n, c['y']['net']['ground_cap'], [math.log1p(T['ground_cap_fF'][x]) for x in n], TOLS['gcap'])
        if 'pin' in c['nodes']:
            n = c['nodes']['pin']
            G[p + 'L_timing_raw_ns'] = value_compare(n, c['y_raw']['pin']['timing'], [pd[i] for i, _ in n], TOLS['timing'])
            G[p + 'L_timing'] = value_compare(n, c['y']['pin']['timing'], [math.log1p(pd[i]) for i, _ in n], TOLS['timing'])
            if v in ('d', 'e'):
                pn = dict(((tuple(a), b) for a, b in T['b_pin_net']))
                G[p + 'L_ground_cap_broadcast_to_pins'] = value_compare(n, c['y_raw']['pin']['ground_cap'], [T['ground_cap_fF'][pn[x]] for x in n], TOLS['gcap'])
        if v == 'c' and c.get('edge_y') is not None:
            rows = [(k, a, b) for k, (a, b, t_) in enumerate(c['edge_ends']) if t_ == 0]
            gates_ = [(a if a[0] == 'gate' else b)[1] for _, a, b in rows]
            G[p + 'L_edge_timing_raw_ns'] = value_compare(gates_, c['edge_y_raw'][[k for k, _, _ in rows], 3], [pd[g] for g in gates_], TOLS['timing'])
    G['present_views'] = dict(passed=set(V) == set('bcdef'), views=sorted(V))
    res = dict(truth=T['design'], verdict=summarize(G), groups=G)
    Path(sys.argv[3]).write_text(json.dumps(res, indent=1, default=float))
    print('DGV1', T['design'][:40], 'PASS' if res['verdict']['passed'] else 'FAIL', res['verdict']['failed_groups'])


if __name__ == '__main__':
    main()
