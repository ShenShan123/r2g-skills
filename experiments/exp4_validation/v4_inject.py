#!/usr/bin/env python3
"""V4: write clean and corrupted COPIES of one design's 10 graphs (never touches the release).

Usage: v4_inject.py RELEASE_ROOT GT.pkl DESIGN OUT_ROOT
Writes OUT_ROOT/<defect>/<design>/<stage>_<view>.pt and OUT_ROOT/<defect>/<design>/paths.json.
Defects simulate a buggy BUILDER, so each is applied consistently to every view that carries
the affected quantity.
"""
import json, pickle, sys
from copy import deepcopy
from pathlib import Path

import torch

STAGES, VIEWS = ('placement', 'route'), 'BCDEF'
UNKNOWN_CELL = 135.0


def col(store, name):
    return list(store.x_schema).index(name)


def edge_cols(store, suffix):
    return [i for i, s in enumerate(store.edge_schema) if s.split('::')[-1] == suffix]


def gate_label_carriers(g, fn):
    """Apply fn(y[N,2], mask[N,2]) -> (y, mask) to Gate labels in every carrier of a view."""
    if 'gate' in g.node_types and 'y' in g['gate']:
        y, m = fn(g['gate'].y.clone(), g['gate'].y_valid_mask.clone(), None)
        g['gate'].y, g['gate'].y_valid_mask, g['gate'].y_task_mask = y, m, m.clone()
    for rel in g.edge_types:
        s = g[rel]
        if 'source_gate_id' in s:
            ids = s.source_gate_id
            y, m = fn(None, None, ids)
            s.edge_y, s.edge_y_valid_mask, s.edge_y_task_mask = y, m, m.clone()


def main():
    rel, gtp, design, out = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
    gt = pickle.load(open(gtp, 'rb'))
    G = {(s, v): torch.load(rel / s / v / f'{design}.pt', map_location='cpu', weights_only=False) for s in STAGES for v in VIEWS}
    B = G['route', 'B']
    gen = torch.Generator().manual_seed(20261107)
    gy0, gm0 = B['gate'].y.clone(), B['gate'].y_valid_mask.clone()
    lab_id = B['gate'].label_grid_id.clone()
    ny, nx = [int(v) for v in B.spatial_shape[0]]
    sp = B.spatial_y.clone()

    def with_gate_y(newy, newm):
        def fn(y, m, ids):
            return (newy.clone(), newm.clone()) if ids is None else (newy[ids].clone(), newm[ids].clone())
        return fn

    defects = {}
    # D1 transposed congestion axes (field keyed (y,x)); gates sampled at their true bin
    def d1(g):
        T = sp.clone()
        if nx == ny:
            idx = torch.arange(nx * ny); ix, iy = idx % nx, idx // nx
            T = sp[ix * nx + iy]
        else:
            T = sp.flip(0)
        y = gy0.clone(); v = lab_id >= 0
        y[v] = T[lab_id[v]]
        if 'spatial_y' in g:
            g.spatial_y = T.clone()
        gate_label_carriers(g, with_gate_y(y, gm0))
    defects['D01_transposed_congestion'] = d1

    def capscale(f):
        def d(g):
            for t in ('pin',):
                if t in g.node_types:
                    g[t].x[:, col(g[t], 'pin_cap_fF')] *= f
            if 'net' in g.node_types:
                g['net'].x[:, col(g['net'], 'total_sink_cap_fF')] *= f
            g.u[0, list(g.u_schema).index('total_lib_pin_cap_fF')] *= f
            for r in g.edge_types:
                for c in edge_cols(g[r], 'pin_cap_fF') + edge_cols(g[r], 'total_sink_cap_fF'):
                    g[r].edge_attr[:, c] *= f
        return d
    defects['D02_pin_cap_x1000'] = capscale(1000.0)
    defects['D03_pin_cap_x0.001'] = capscale(0.001)

    gnames = list(B['gate'].names)
    esc = torch.tensor([('[' in n or '/' in n) for n in gnames])

    def d4(g):
        y = gy0.clone(); m = gm0.clone()
        y[esc] = float('nan'); m[esc] = False
        gate_label_carriers(g, with_gate_y(y, m))
        wesc = torch.tensor([('[' in n or '/' in n) for n in g.wl_group_names])
        g.wl_group_y[wesc] = float('nan'); g.wl_group_valid_mask[wesc] = False; g.wl_group_task_mask[wesc] = False
    defects['D04_escaped_name_join_drop'] = d4

    perm = torch.randperm(len(gnames), generator=gen)

    def d5(g):
        gate_label_carriers(g, with_gate_y(gy0[perm], gm0[perm]))
        if 'gate' in g.node_types and 'label_grid_id' in g['gate']:
            g['gate'].label_grid_id = lab_id[perm].clone()
    defects['D05_shuffled_gate_labels'] = d5

    comps = gt['stages']['5_route']['comps']; dbu = gt['stages']['5_route']['dbu']

    def d6(g):
        if g.prediction_stage != 'placement' or 'gate' not in g.node_types:
            return
        s = g['gate']
        for k, n in enumerate(s.names):
            c = comps.get(n)
            if c is None or c['x'] is None:
                continue
            for name, val in (('x_um', c['x'] / dbu), ('y_um', c['y'] / dbu), ('center_x_um', c['x'] / dbu), ('center_y_um', c['y'] / dbu)):
                s.x[k, col(s, name)] = val; s.x_valid_mask[k, col(s, name)] = True
            s.x[k, col(s, 'placement_valid')] = 1.0
    defects['D06_route_positions_leak_into_placement'] = d6

    def d7(g):
        y = gy0.clone(); y[gm0[:, 0], 0] = 0.0
        gate_label_carriers(g, with_gate_y(y, gm0))
    defects['D07_zero_filled_label_valid'] = d7

    def d8(g):
        y = gy0.clone(); y[:, 1] = float('nan'); m = gm0.clone(); m[:, 1] = True
        gate_label_carriers(g, with_gate_y(y, m))
    defects['D08_nan_label_marked_valid'] = d8

    def d9(g):
        if g.prediction_stage != 'route':
            return
        for t, cols_ in (('gate', ('x_um', 'y_um', 'center_x_um', 'center_y_um')), ('pin', ('pin_x_um', 'pin_y_um')), ('io_pin', ('pin_x_um', 'pin_y_um'))):
            if t in g.node_types:
                for c in cols_:
                    g[t].x[:, col(g[t], c)] *= 2.0
        for c, f in (('die_width_um', 2.0), ('die_height_um', 2.0), ('die_area_um2', 4.0), ('dbu_per_um', 0.5)):
            g.u[0, list(g.u_schema).index(c)] *= f
        for r in g.edge_types:
            for c in edge_cols(g[r], 'pin_x_um') + edge_cols(g[r], 'pin_y_um') + edge_cols(g[r], 'x_um') + edge_cols(g[r], 'y_um'):
                g[r].edge_attr[:, c] *= 2.0
    defects['D09_wrong_die_scale_dbu'] = d9

    def d10(g):
        g.y[0, 2] = min(0.0, float(g.y[0, 2])) if g.y_valid_mask[0, 2] else g.y[0, 2]
    defects['D10_wns_clipped_sign_convention'] = d10

    def d11(g):
        if gt['sta']['grt_ws'] is not None and g.y_valid_mask[0, 2]:
            g.y[0, 2] = float(gt['sta']['grt_ws'])
    defects['D11_timing_from_wrong_stage'] = d11

    v = torch.nonzero(B.wl_group_valid_mask[:, 0]).flatten()
    pick = v[torch.randperm(len(v), generator=gen)[:max(1, len(v) // 20)]]

    def d12(g):
        g.wl_group_y[pick, 0] *= 1.10
    defects['D12_wirelength_via_patch_inflation'] = d12

    PN = ('pin', 'connects_to', 'net')
    npn = B[PN].edge_index.shape[1]
    drop = torch.randperm(npn, generator=gen)[:max(1, npn // 100)]
    keep = torch.ones(npn, dtype=torch.bool); keep[drop] = False
    dropped_pins = set(B[PN].edge_index[0, drop].tolist())

    def d13(g):
        if g.view_name == 'B':
            s = g[PN]; s.edge_index = s.edge_index[:, keep].contiguous(); s.edge_attr = s.edge_attr[keep]
        if g.view_name == 'C':
            s = g['gate', 'pin_incidence', 'net']
            k2 = torch.tensor([int(p) not in dropped_pins for p in s.source_pin_id.tolist()])
            for key in ('edge_index',):
                s[key] = s[key][:, k2].contiguous()
            for key in ('edge_attr', 'edge_attr_valid_mask', 'source_pin_id'):
                s[key] = s[key][k2]
    defects['D13_dropped_pin_net_edges'] = d13

    from collections import Counter
    m = Counter(gt['canon']['masters'].values()).most_common(1)[0][0]
    victims = {n for n, mm in gt['canon']['masters'].items() if mm == m}

    def d14(g):
        if 'gate' in g.node_types:
            s = g['gate']; c = col(s, 'cell_type_id')
            for k, n in enumerate(s.names):
                if n in victims:
                    s.x[k, c] = UNKNOWN_CELL
        if 'pin' in g.node_types:
            s = g['pin']; c = col(s, 'cell_type_id')
            for k, n in enumerate(s.names):
                if n[0] in victims:
                    s.x[k, c] = UNKNOWN_CELL
    defects['D14_cell_type_collapse_unknown'] = d14

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from v2_compare import field_diff
    import shutil
    for name, fn in [('clean', None)] + list(defects.items()):
        d = out / name / design; d.mkdir(parents=True, exist_ok=True)
        paths = {}
        for (s, vw), g in G.items():
            p = d / f'{s}_{vw}.pt'
            h = deepcopy(g)
            if fn is not None:
                fn(h)
            if fn is None or not field_diff(g, h):
                shutil.copyfile(rel / s / vw / f'{design}.pt', p)  # untouched graph: byte copy of the release
            else:
                torch.save(h, p)
            paths[f'{s}/{vw}'] = str(p)
        (d / 'paths.json').write_text(json.dumps(paths, indent=1))
    print('INJECT', design, len(defects), 'defects + clean')


if __name__ == '__main__':
    main()
