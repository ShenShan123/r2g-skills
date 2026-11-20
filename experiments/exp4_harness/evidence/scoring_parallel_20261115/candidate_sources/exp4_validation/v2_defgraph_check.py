#!/usr/bin/env python3
"""Exp-4 v2 check of a def-graph-format b-f dataset against truth_defgraph.py ground truth.

Scores EVERY form present: homo `<v>_graph_homo.pt` (or a homogeneous `<v>_graph.pt`) and the
HeteroData `<v>_graph.pt`. Each form is scored by the same rules through adapter_defgraph.load_views_v2.

Usage: v2_defgraph_check.py TRUTH.pkl DATASET_DIR OUT.json [--views bcdef]

Tolerances are frozen in PROTOCOL.md section v2 (TOLS below). A group passes iff every entity
matches. Power is out of scope: cell_power (gate x4, e-view gate edge_attr[2]) and IR drop
(slot 2 values) are not scored. The NaN placement of the IR-drop slot on non-gate rows is
structure, and it IS scored.
"""
import argparse, json, math, pickle, sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match import set_compare, value_compare, summarize  # noqa: E402
from adapter_defgraph import load_views_v2, SLOTS  # noqa: E402

# FROZEN 2026-11-07 (PROTOCOL.md v2 §T). (atol, rtol): pass iff |a-b| <= atol + rtol*|b|.
TOLS = {'coord': (1e-4, 1e-6), 'hpwl': (6e-3, 1e-6), 'lib': (1e-6, 1e-5), 'cong': (1e-6, 1e-5),
        'timing': (1e-4, 1e-6), 'wl_um': (2e-4, 1e-6), 'wl_label': (1e-6, 1e-6), 'gcap': (1e-5, 1e-5),
        'ccap': (1e-5, 1e-5), 'res': (1e-4, 1e-5), 'rc_label': (1e-6, 1e-5), 'exact': (0, 0)}
FEAT_TOL = dict(cell_type_id='exact', cell_area='lib', x_um='coord', y_um='coord', orientation_id='exact',
                placement_status_id='exact', net_type_id='exact', fanout='exact', pin_count='exact', num_drivers='exact',
                num_sinks='exact', connects_macro_flag='exact', num_layer='exact', hpwl_um='hpwl', pin_x_um='coord', pin_y_um='coord',
                nearest_tap_distance_um='coord', pin_direction_id='exact', pin_type_id='exact', sum_pin_cap_fF='lib')
POWER = {'cell_power'}
TYPE_FEATS = dict(gate=['cell_type_id', 'cell_area', 'x_um', 'y_um', 'orientation_id', 'placement_status_id'],
                  net=['net_type_id', 'fanout', 'pin_count', 'num_drivers', 'num_sinks', 'connects_macro_flag', 'num_layer', 'hpwl_um'],
                  iopin=['pin_x_um', 'pin_y_um', 'nearest_tap_distance_um', 'pin_direction_id'], pin=['pin_type_id', 'sum_pin_cap_fF'])
GATE_COLS = ['cell_type_id', 'cell_area', 'cell_power', 'x_um', 'y_um', 'orientation_id', 'placement_status_id', None]
NET_COLS = ['net_type_id', 'fanout', 'pin_count', 'num_drivers', 'num_sinks', 'connects_macro_flag', 'num_layer', 'hpwl_um']
PIN_COLS = ['pin_type_id', 'sum_pin_cap_fF', None, None, None, None, None, None]
VIEW_TYPES = dict(b=('gate', 'net', 'iopin', 'pin'), c=('gate', 'net', 'iopin'), d=('gate', 'iopin', 'pin'), e=('iopin', 'pin'), f=('gate', 'iopin'))
# which node type carries each label slot, per view (graph-dataset.md "Shared tensor schema")
LABEL_AT = {v: dict(congestion={'gate'}, irdrop={'gate'}, timing={'pin'}, wirelength={'net'},
                    ground_cap={'net'} if v in 'bc' else {'pin'} if v in 'de' else set()) for v in 'bcdef'}
NAN = math.nan


def k_(e):
    return repr(e)


def truth_edge_rows(T, view, fam, ent):
    """Expected (attr[8], edge_y[5], edge_y_raw[5]) of one undirected edge; None = column not scored."""
    z8, n5 = [0.0] * 8, [NAN] * 5
    if fam in ('connects', 'iopin_connection', 'gate_pin'):
        return z8, n5, n5
    if fam == 'pin':
        px = T['pin_x'][ent]; g = ent[0]; pd = T['path_delay'][g]
        return [px['pin_type_id'], px['sum_pin_cap_fF']] + [0.0] * 6, [NAN, NAN, math.log1p(pd), NAN, NAN], [NAN, NAN, pd, NAN, NAN]
    if fam == 'net':
        nx = T['net_x'][ent]; wl = T['wirelength_um'][ent]
        return [nx[c] for c in NET_COLS], [NAN, NAN, NAN, math.log1p(wl), NAN], [NAN, NAN, NAN, wl, NAN]
    if fam == 'gate':
        gx = T['gate_x'][ent]; cg = T['congestion'][ent]
        return [gx[c] if c else 0.0 for c in GATE_COLS], [cg[1], NAN, NAN, NAN, NAN], [cg[0], NAN, NAN, NAN, NAN]
    raise KeyError(fam)


def attr_tol(fam, j):
    col = {'pin': PIN_COLS, 'net': NET_COLS, 'gate': GATE_COLS}.get(fam, [None] * 8)[j]
    if col in POWER:
        return None
    return TOLS[FEAT_TOL[col]] if col else TOLS['exact']


LBL_TOL = {'congestion': ('cong', 'cong'), 'timing': ('timing', 'timing'), 'wirelength': ('wl_label', 'wl_um'), 'ground_cap': ('gcap', 'gcap')}


def sort_rows(rows):
    return sorted(rows, key=lambda r: tuple((1e300 if v != v else round(float(v), 3)) for v in r[0] + r[1]))


def _canonical_row(values):
    # Exact float64 nan_to_num semantics without one NumPy dispatch per edge.
    largest = 1.7976931348623157e308
    return tuple(-7.0 if math.isnan(x) else largest if x == math.inf
                 else -largest if x == -math.inf else x for x in map(float, values))


def check_view(T, v, c, G, p, *, check_folded=True):
    keyset = dict(gate=set(T['gates']), net=set(T['nets']), iopin=set(T['iopins']), pin=set(map(tuple, T['pins'])))
    order = T['order']
    for t in VIEW_TYPES[v]:
        G[p + 'I_' + t] = set_compare(c['nodes'].get(t, []), order[t])
        G[p + 'O_' + t] = dict(passed=list(c['nodes'].get(t, [])) == list(order[t]), n=len(order[t]))
    extra = set(c['nodes']) - set(VIEW_TYPES[v])
    G[p + 'I_no_extra_node_types'] = dict(passed=not extra, extra=sorted(extra))
    if c['form'] == 'homo':
        blocks = [(t, n) for t in VIEW_TYPES[v] for n in order[t]]
        G[p + 'O_global_block_layout'] = dict(passed=c['global_order'] == blocks)
        for k in ('y0_is_node_type', 'y_raw0_is_node_type', 'edges_interleaved', 'edge_pair_rows_identical',
                  'edge_y0_is_edge_type', 'edge_y_raw0_is_edge_type'):
            G[p + 'S_' + k] = dict(passed=bool(c['inv'].get(k)))
        G[p + 'S_x_y_width'] = dict(passed=c['inv']['x_width'] == 10 and c['inv']['y_width'] == 6, x=c['inv']['x_width'], y=c['inv']['y_width'])
        G[p + 'S_rc_tensors_present'] = dict(passed=bool(c['inv'].get('rc_present')))
        if c['rc']:
            G[p + 'S_rc_interleaved'] = dict(passed=bool(c['inv'].get('rc_interleaved')))
            G[p + 'S_rc_y0_is_rc_type'] = dict(passed=bool(c['inv'].get('rc_y0_is_rc_type')))
    else:
        w = c['inv']['widths']
        G[p + 'S_hetero_widths'] = dict(passed=all(x.get('x') == 9 and x.get('y') == 5 and x.get('y_raw') == 5 for x in w.values()), widths=w)
        eyw = c['inv'].get('edge_y_widths', set())
        G[p + 'S_hetero_edge_y_width'] = dict(passed=eyw <= {5}, widths=sorted(eyw))
        G[p + 'S_graph_kind'] = dict(passed=c['inv'].get('graph_kind') == 'hetero', value=c['inv'].get('graph_kind'))
    # ---- features
    tx = dict(gate=T['gate_x'], net=T['net_x'], iopin=T['iopin_x'], pin=T['pin_x'])
    for t in VIEW_TYPES[v]:
        names = c['nodes'].get(t, [])
        cols = c['x'].get(t, {})
        for col in TYPE_FEATS[t]:
            if col not in cols:
                G[f'{p}F_{t}_{col}'] = dict(passed=False, error='column missing'); continue
            truth = [tx[t].get(n, {}).get(col, NAN) for n in names]
            G[f'{p}F_{t}_{col}'] = value_compare(names, cols[col], truth, TOLS[FEAT_TOL[col]])
    # ---- node labels + NaN placement
    pnet = T['pin_net']
    for t in VIEW_TYPES[v]:
        names = c['nodes'].get(t, [])
        for which in ('y', 'y_raw'):
            ys = c[which].get(t)
            if ys is None:
                G[f'{p}L_{which}_{t}_present'] = dict(passed=False); continue
            for s in SLOTS:
                arr = np.asarray(ys[s], float)
                if t not in LABEL_AT[v][s]:
                    G[f'{p}L_nan_{which}_{t}_{s}'] = dict(passed=bool(np.isnan(arr).all()), n=int(arr.size), non_nan=int((~np.isnan(arr)).sum()))
                    continue
                if s == 'irdrop':
                    continue                                  # power: not scored
                raw = which == 'y_raw'
                # A12 (2026-11-09): an entity the truth does not know (e.g. a clock net the spec excludes) is a
                # FAILED position of this group -- before, the KeyError crashed the design's whole graph check and
                # every group of it went unrecorded. The lookups are exactly the original ones; a NaN claim gets
                # no NaN==NaN credit at an unknown entity.
                def lookup(x):
                    try:
                        if s == 'congestion':
                            return T['congestion'][x][0 if raw else 1]
                        if s == 'timing':
                            return T['path_delay'][x[0]]
                        if s == 'wirelength':
                            return T['wirelength_um'][x]
                        return T['ground_cap_fF'][x if t == 'net' else pnet[x]]
                    except (KeyError, IndexError, TypeError):
                        return None
                tr, unknown_pos = [], []
                for i, x in enumerate(names):
                    val = lookup(x)
                    if val is None:
                        unknown_pos.append(i); tr.append(NAN)
                    else:
                        tr.append(val if (raw or s == 'congestion') else math.log1p(val))
                res = value_compare(names, arr, tr, TOLS[LBL_TOL[s][1 if raw else 0]])
                if unknown_pos:
                    credited = sum(1 for i in unknown_pos if i < arr.size and np.isnan(arr[i]))
                    res['matched'] = max(int(res.get('matched', 0)) - credited, 0)
                    res['rate'] = res['matched'] / res['n'] if res.get('n') else 0.0
                    res['passed'] = False
                    res['unknown_entities'] = len(unknown_pos)
                    res['unknown_examples'] = [str(names[i]) for i in unknown_pos[:5]]
                G[f'{p}L_{which}_{t}_{s}'] = res
    # ---- edges: symmetry, topology per family, folded rows
    dirs = Counter((f, a, b, r) for f, a, b, *rr in c['edges'] for r in [_canonical_row(sum(map(list, rr), []))])
    rev = Counter((f, b, a, r) for (f, a, b, r), m in dirs.items() for _ in range(m))
    G[p + 'S_edges_reverse_symmetric'] = dict(passed=dirs == rev, n_directed=len(c['edges']))
    und = [(f, a, b, at, ey, eyr) for f, a, b, at, ey, eyr in c['edges'] if k_(a) < k_(b)]
    tv = T['views'][v]
    G[p + 'T_families'] = dict(passed={f for f, *_ in und} == {f for f, L in tv.items() if L}, claim=sorted({f for f, *_ in und}), truth=sorted(f for f, L in tv.items() if L))
    for fam, lst in tv.items():
        tr = [(fam, frozenset((a, b))) for a, b, _ in lst]
        cl = [(f, frozenset((a, b))) for f, a, b, *_ in und if f == fam]
        G[f'{p}T_{fam}'] = set_compare(cl, tr)
        if v == 'b' or not check_folded:
            continue
        cb, tb = defaultdict(list), defaultdict(list)
        for f, a, b, at, ey, eyr in und:
            if f == fam:
                cb[frozenset((a, b))].append((list(at), list(ey), list(eyr)))
        for a, b, ent in lst:
            tb[frozenset((a, b))].append(truth_edge_rows(T, v, fam, ent))
        pairs = []
        for key, trows in tb.items():
            crows = cb.get(key, [])
            if len(crows) != len(trows):
                continue
            pairs += list(zip(sort_rows(crows), sort_rows(trows)))
        G[f'{p}E_{fam}_aligned'] = dict(passed=len(pairs) == len(lst), aligned=len(pairs), truth=len(lst))
        if not pairs:
            continue
        names = [None] * len(pairs)
        for j in range(8):
            tol = attr_tol(fam, j)
            if tol is None:
                continue
            col = ({'pin': PIN_COLS, 'net': NET_COLS, 'gate': GATE_COLS}.get(fam, [None] * 8)[j]) or f'pad{j}'
            G[f'{p}E_{fam}_attr{j}_{col}'] = value_compare(names, [cr[0][j] for cr, _ in pairs], [tr_[0][j] for _, tr_ in pairs], tol)
        for which, idx in (('edge_y', 1), ('edge_y_raw', 2)):
            for s_i, s in enumerate(SLOTS):
                ca = [cr[idx][s_i] for cr, _ in pairs]; ta = [tr_[idx][s_i] for _, tr_ in pairs]
                if s == 'irdrop' and fam == 'gate':
                    continue                                  # power: not scored
                if all(x != x for x in ta):
                    G[f'{p}E_{fam}_{which}_{s}_nan'] = dict(passed=all(x != x for x in ca))
                    continue
                if s == 'irdrop':
                    continue
                tol = TOLS[LBL_TOL[s][1 if which == 'edge_y_raw' else 0]]
                G[f'{p}E_{fam}_{which}_{s}'] = value_compare(names, ca, ta, tol)
    # ---- RC parasitic edges
    rc_truth = defaultdict(dict)
    iop, pins_ = keyset['iopin'], keyset['pin']
    if v in 'bc':
        for (na, nb), val in T['rc_coupling_nets'].items():
            if na in keyset['net'] and nb in keyset['net']:
                rc_truth['rc_coupling'][frozenset((('net', na), ('net', nb)))] = val
    elif v in 'de':
        def drv_node(n):
            d = T['rc_driver'].get(n)
            if d is None:
                return None
            if d[0] == 'PIN':
                return ('iopin', d[1]) if d[1] in iop else None
            return ('pin', d) if d in pins_ else None
        for (na, nb), val in T['rc_coupling_nets'].items():
            a, b = drv_node(na), drv_node(nb)
            if a is not None and b is not None and a != b:
                rc_truth['rc_coupling'][frozenset((a, b))] = val
    if v in 'bde':
        def pnode(k):
            if k[0] == 'PIN':
                return ('iopin', k[1]) if k[1] in iop else None
            return ('pin', k) if k in pins_ else None
        for (ka, kb), ohm in T['rc_res_pairs'].items():
            a, b = pnode(ka), pnode(kb)
            if a is not None and b is not None and a != b:
                rc_truth['rc_resistance'][frozenset((a, b))] = ohm
    rcd = Counter((f, a, b, _canonical_row(y + yr)) for f, a, b, y, yr in c['rc'])
    rcr = Counter((f, b, a, r) for (f, a, b, r), m in rcd.items() for _ in range(m))
    G[p + 'S_rc_reverse_symmetric'] = dict(passed=rcd == rcr, n_directed=len(c['rc']))
    rund = [(f, a, b, y, yr) for f, a, b, y, yr in c['rc'] if k_(a) < k_(b)]
    for fam, col in (('rc_coupling', 0), ('rc_resistance', 1)):
        tr = rc_truth.get(fam, {})
        cl = [(frozenset((a, b)), y, yr) for f, a, b, y, yr in rund if f == fam]
        G[f'{p}R_{fam}_edges'] = set_compare([k for k, _, _ in cl], list(tr))
        al = [(y, yr, tr[k]) for k, y, yr in cl if k in tr]
        if not al:
            continue
        names = [None] * len(al)
        ts = (('ccap', 'rc_label') if fam == 'rc_coupling' else ('res', 'rc_label'))
        G[f'{p}R_{fam}_raw'] = value_compare(names, [a[1][col] for a in al], [a[2] for a in al], TOLS[ts[0]])
        G[f'{p}R_{fam}_label'] = value_compare(names, [a[0][col] for a in al], [math.log1p(a[2]) for a in al], TOLS[ts[1]])
        G[f'{p}R_{fam}_offtype_nan'] = dict(passed=all(a[0][1 - col] != a[0][1 - col] and a[1][1 - col] != a[1][1 - col] for a in al))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('truth'); ap.add_argument('dataset'); ap.add_argument('out')
    ap.add_argument('--views', default='bcdef'); ap.add_argument('--forms', default='homo,hetero')
    a = ap.parse_args()
    T = pickle.load(open(a.truth, 'rb'))
    F = load_views_v2(a.dataset, a.views)
    G = {}
    for form in a.forms.split(','):
        V = F.get(form, {})
        G[f'{form}.present_views'] = dict(passed=set(V) == set(a.views), views=sorted(V))
        for v, c in sorted(V.items()):
            check_view(T, v, c, G, f'{form}.{v}.')
    res = dict(truth=T['design'], platform=T.get('platform'), verdict=summarize(G), groups=G, spef_stats=T.get('spef_stats'))
    Path(a.out).write_text(json.dumps(res, indent=1, default=lambda o: float(o) if isinstance(o, (np.floating, np.integer)) else str(o)))
    print('DGV2', T['design'][:40], T.get('platform'), 'PASS' if res['verdict']['passed'] else 'FAIL',
          res['verdict']['n_groups'], res['verdict']['failed_groups'][:40])


if __name__ == '__main__':
    main()
