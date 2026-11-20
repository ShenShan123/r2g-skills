"""Thin adapter: def-graph (R2GSkills) dataset outputs -> neutral, name-keyed claims.

Used to score ANY converter that emits the def-graph format (the R2GSkills skill itself and the
LLM-written converters of the conversion study). Inputs are only the emitted files:

  b-f views   <dataset>/{b..f}_graph_homo.pt (homogeneous Data; if only `<v>_graph.pt` exists and is
              homogeneous it is used instead). Node identity = `node_name` (DEF names, escapes
              allowed: every backslash is removed); pin nodes are "inst/pin" (split at the LAST '/').
              Node type = x[:,0] (0 gate, 1 net, 2 iopin, 3 pin). Feature slots follow
              x_schema (gate/net/iopin/pin `_x2_9` lists) when present, else the documented order.
  four-stage  <dataset_4stage>/generated/stages/<stage>/heterograph.pt (HeteroData; names in
              gate.inst_name, net.net_name, io_pin.iopin_name, pin.(inst_name, pin_name)).
  images      <images>/{features,labels}.npz + image_meta.json + labels/congestion_grid.json.

Claims layout (all names canonical = backslash-free):
  views[v] = dict(nodes={type: [names]}, x={type: {col: array}}, y={type: {slot: array}},
                  y_raw=..., edges=Counter{(edge_type, frozenset{(ntype, name), (ntype, name)})})
"""
import math
from collections import Counter
from pathlib import Path

import numpy as np

TYPES = {0: 'gate', 1: 'net', 2: 'iopin', 3: 'pin'}
DOC_COLS = {'gate': ['cell_type_id', 'cell_area', 'cell_power', 'x_um', 'y_um', 'orientation_id', 'placement_status_id', '_pad7'],
            'net': ['net_type_id', 'fanout', 'pin_count', 'num_drivers', 'num_sinks', 'connects_macro_flag', 'num_layer', 'hpwl_um'],
            'iopin': ['pin_x_um', 'pin_y_um', 'nearest_tap_distance_um', 'pin_direction_id', '_pad4', '_pad5', '_pad6', '_pad7'],
            'pin': ['pin_type_id', 'sum_pin_cap_fF', '_pad2', '_pad3', '_pad4', '_pad5', '_pad6', '_pad7']}
Y_SLOTS = {1: 'congestion', 2: 'irdrop', 3: 'timing', 4: 'wirelength', 5: 'ground_cap'}


def canon(s):
    return str(s).replace('\\', '')


def node_key(t, name):
    if t == 'pin':
        i, p = str(name).rsplit('/', 1)
        return (canon(i), canon(p))
    return canon(name)


def load_view(path):
    import torch
    g = torch.load(path, map_location='cpu', weights_only=False)
    if hasattr(g, 'node_types') and g.node_types:   # hetero: not the source of truth here
        raise ValueError(f'{path} is HeteroData; pass the homogeneous file')
    x = g.x.detach().cpu().numpy().astype(np.float64)
    nt = x[:, 0].astype(int)
    names = list(g.node_name)
    sch = getattr(g, 'x_schema', None)
    sch = sch if isinstance(sch, dict) else {}   # A10 (conversion agent, 2026-11-07): a non-dict x_schema -> documented order, not a crash
    cols = {t: list(sch.get(f'{t}_x2_9', DOC_COLS[t])) for t in DOC_COLS}
    out = dict(nodes={}, x={}, y={}, y_raw={}, index={}, edges=Counter(), edge_rows={})
    y = g.y.detach().cpu().numpy().astype(np.float64) if hasattr(g, 'y') else None
    yr = g.y_raw.detach().cpu().numpy().astype(np.float64) if hasattr(g, 'y_raw') else None
    keys = []
    for k, t in TYPES.items():
        idx = np.flatnonzero(nt == k)
        if not len(idx):
            continue
        out['nodes'][t] = [node_key(t, names[i]) for i in idx]
        out['index'][t] = idx
        out['x'][t] = {c: x[idx, 2 + j] for j, c in enumerate(cols[t]) if not c.startswith('_pad')}
        if y is not None:
            out['y'][t] = {s: y[idx, j] for j, s in Y_SLOTS.items()}
        if yr is not None:
            out['y_raw'][t] = {s: yr[idx, j] for j, s in Y_SLOTS.items()}
    keys = [(TYPES[int(nt[i])], node_key(TYPES[int(nt[i])], names[i])) for i in range(len(names))]
    ei = g.edge_index.detach().cpu().numpy()
    et = g.edge_type.detach().cpu().numpy() if hasattr(g, 'edge_type') and g.edge_type is not None else np.zeros(ei.shape[1], int)
    # undirected edges appear as adjacent (fwd, rev) columns: count each undirected edge once
    for c in range(0, ei.shape[1], 2):
        a, b = int(ei[0, c]), int(ei[1, c])
        out['edges'][(int(et[c]), frozenset([keys[a], keys[b]]))] += 1
    out['directed_pairs_ok'] = bool(ei.shape[1] % 2 == 0 and np.array_equal(ei[0, 0::2], ei[1, 1::2]) and np.array_equal(ei[1, 0::2], ei[0, 1::2]))
    if hasattr(g, 'edge_y') and g.edge_y is not None:
        out['edge_y'] = g.edge_y.detach().cpu().numpy().astype(np.float64)
        out['edge_y_raw'] = g.edge_y_raw.detach().cpu().numpy().astype(np.float64) if hasattr(g, 'edge_y_raw') else None
        out['edge_attr'] = g.edge_attr.detach().cpu().numpy().astype(np.float64) if hasattr(g, 'edge_attr') else None
        out['edge_ends'] = [(keys[int(ei[0, c])], keys[int(ei[1, c])], int(et[c])) for c in range(ei.shape[1])]
    out['node_order'] = keys
    return out


def load_views(dataset_dir, views='bcdef'):
    d = Path(dataset_dir); res = {}
    for v in views:
        for name in (f'{v}_graph_homo.pt', f'{v}_graph.pt'):
            p = d / name
            if p.exists():
                try:
                    res[v] = load_view(p); break
                except ValueError:
                    continue
    return res


def load_four_stage(generated_dir):
    """{stage: claims} for the R2G2.0 four-stage HeteroData graphs."""
    import torch
    out = {}
    for st in ('floorplan', 'placement', 'cts', 'route'):
        p = Path(generated_dir) / 'stages' / st / 'heterograph.pt'
        if not p.exists():
            continue
        g = torch.load(p, map_location='cpu', weights_only=False)
        c = dict(nodes={}, x={}, y={}, mask={}, edges={})
        for t, keyf in (('gate', lambda s: [canon(n) for n in s.inst_name]), ('net', lambda s: [canon(n) for n in s.net_name]),
                        ('io_pin', lambda s: [canon(n) for n in s.iopin_name]),
                        ('pin', lambda s: [(canon(a), canon(b)) for a, b in zip(s.inst_name, s.pin_name)])):
            if t not in g.node_types:
                continue
            s = g[t]; c['nodes'][t] = keyf(s)
            x = s.x.detach().numpy().astype(np.float64)
            c['x'][t] = {col: x[:, j] for j, col in enumerate(s.x_schema)}
            if 'y' in s:
                y = s.y.detach().numpy().astype(np.float64); m = s.y_valid_mask.detach().numpy()
                c['y'][t] = {col: y[:, j] for j, col in enumerate(s.y_schema)}
                c['mask'][t] = {col: m[:, j] for j, col in enumerate(s.y_schema)}
        for rel in g.edge_types:
            ei = g[rel].edge_index.detach().numpy()
            sa, sb = c['nodes'].get(rel[0]), c['nodes'].get(rel[2])
            if sa is None or sb is None:
                continue
            c['edges']['|'.join(rel)] = [(sa[i], sb[j]) for i, j in ei.T]
        c['meta'] = {k: (v if not hasattr(v, 'tolist') else v.tolist()) for k, v in g._global_store.items() if not hasattr(v, 'shape') or v.numel() < 64}
        out[st] = c
    return out


def load_images(images_dir):
    d = Path(images_dir)
    f = dict(np.load(d / 'features.npz', allow_pickle=False)); lb = dict(np.load(d / 'labels.npz', allow_pickle=False))
    import json
    meta = json.loads((d / 'image_meta.json').read_text()) if (d / 'image_meta.json').exists() else {}
    return dict(features=f, labels=lb, meta=meta)


# ============================================================================ v2 (2026-11-07)
# Both emitted forms -> one neutral claim layout, so the same check scores either:
#   homo   `<v>_graph_homo.pt` (R2G_GRAPH_KIND=both) or a homogeneous `<v>_graph.pt`
#   hetero a HeteroData `<v>_graph.pt` (the def-graph default)
# Edge families use the DOCUMENTED names (graph-dataset.md "Heterogeneous graphs"): for homo the
# edge_type id is mapped through this table (the emitted edge_schema is not consulted). For
# hetero, the relation in the store key is taken as written, and the check scores it against
# the same table.
FAMILY = {'b': {None: 'connects', 0: 'connects'}, 'c': {0: 'pin', 1: 'iopin_connection'},
          'd': {0: 'gate_pin', 1: 'net'}, 'e': {0: 'gate', 1: 'net'}, 'f': {0: 'net'}}
RC_FAMILY = {0: 'rc_coupling', 1: 'rc_resistance'}
SLOTS = ['congestion', 'irdrop', 'timing', 'wirelength', 'ground_cap']   # y1..y5 / hetero y0..y4
NT_ID = {'gate': 0, 'net': 1, 'iopin': 2, 'pin': 3}


def _np(t):
    return None if t is None else t.detach().cpu().numpy()


def _homo_claims(g, view):
    x = _np(g.x).astype(np.float64); nt = x[:, 0].astype(int)
    names = list(g.node_name)
    sch = getattr(g, 'x_schema', None)
    sch = sch if isinstance(sch, dict) else {}   # A10 (conversion agent, 2026-11-07): a non-dict x_schema -> documented order, not a crash
    keys = [(TYPES[int(t)], node_key(TYPES[int(t)], n)) for t, n in zip(nt, names)]
    c = dict(form='homo', nodes={}, raw_names={}, x={}, y={}, y_raw={}, global_order=keys, inv={})
    y = _np(getattr(g, 'y', None)); yr = _np(getattr(g, 'y_raw', None))
    c['inv']['x0_is_node_type'] = True
    c['inv']['y0_is_node_type'] = bool(y is not None and np.array_equal(y[:, 0], x[:, 0]))
    c['inv']['y_raw0_is_node_type'] = bool(yr is not None and np.array_equal(yr[:, 0], x[:, 0]))
    c['inv']['x_width'] = int(x.shape[1]); c['inv']['y_width'] = None if y is None else int(y.shape[1])
    for k, t in TYPES.items():
        idx = np.flatnonzero(nt == k)
        if not len(idx):
            continue
        c['nodes'][t] = [keys[i][1] for i in idx]; c['raw_names'][t] = [names[i] for i in idx]
        cols = list(sch.get(f'{t}_x2_9', DOC_COLS[t]))
        c['x'][t] = {col: x[idx, 2 + j] for j, col in enumerate(cols)}
        if y is not None:
            c['y'][t] = {s: y[idx, 1 + j] for j, s in enumerate(SLOTS)}
        if yr is not None:
            c['y_raw'][t] = {s: yr[idx, 1 + j] for j, s in enumerate(SLOTS)}
    ei = _np(g.edge_index)
    E = ei.shape[1]
    et = _np(getattr(g, 'edge_type', None))
    ea = _np(getattr(g, 'edge_attr', None)); ey = _np(getattr(g, 'edge_y', None)); eyr = _np(getattr(g, 'edge_y_raw', None))
    fam = FAMILY[view]
    nan5 = (math.nan,) * 5
    edges = []
    for k in range(E):
        t_ = None if et is None else int(et[k])
        edges.append((fam.get(t_, f'etype{t_}'), keys[int(ei[0, k])], keys[int(ei[1, k])],
                      tuple(ea[k]) if ea is not None else (0.0,) * 8,
                      tuple(ey[k, 1:]) if ey is not None else nan5, tuple(eyr[k, 1:]) if eyr is not None else nan5))
    c['edges'] = edges
    c['inv']['edges_interleaved'] = bool(E % 2 == 0 and np.array_equal(ei[0, 0::2], ei[1, 1::2]) and np.array_equal(ei[1, 0::2], ei[0, 1::2]))
    pair_rows_same = True
    for arr in (ea, ey, eyr, et):
        if arr is not None and E % 2 == 0 and E:
            a, b = arr[0::2], arr[1::2]
            pair_rows_same &= bool(np.array_equal(np.nan_to_num(a, nan=-7.0), np.nan_to_num(b, nan=-7.0)))
    c['inv']['edge_pair_rows_identical'] = pair_rows_same
    c['inv']['edge_y0_is_edge_type'] = bool(ey is None or et is None or np.array_equal(ey[:, 0], et.astype(float)))
    c['inv']['edge_y_raw0_is_edge_type'] = bool(eyr is None or et is None or np.array_equal(eyr[:, 0], et.astype(float)))
    rei = _np(getattr(g, 'rc_edge_index', None)); rt = _np(getattr(g, 'rc_edge_type', None))
    ry = _np(getattr(g, 'rc_edge_y', None)); ryr = _np(getattr(g, 'rc_edge_y_raw', None))
    rc = []
    if rei is not None and rei.shape[1]:
        for k in range(rei.shape[1]):
            rc.append((RC_FAMILY.get(int(rt[k]), f'rc_type{int(rt[k])}'), keys[int(rei[0, k])], keys[int(rei[1, k])],
                       tuple(ry[k, 1:]), tuple(ryr[k, 1:]) if ryr is not None else (math.nan, math.nan)))
        R = rei.shape[1]
        c['inv']['rc_interleaved'] = bool(R % 2 == 0 and np.array_equal(rei[0, 0::2], rei[1, 1::2]) and np.array_equal(rei[1, 0::2], rei[0, 1::2]))
        c['inv']['rc_y0_is_rc_type'] = bool(np.array_equal(ry[:, 0], rt.astype(float)))
    c['inv']['rc_present'] = rei is not None and rt is not None and ry is not None
    c['rc'] = rc
    return c


def _hetero_claims(h, view):
    c = dict(form='hetero', nodes={}, raw_names={}, x={}, y={}, y_raw={}, global_order=None, inv={}, relations=[])
    xs = getattr(h, 'x_schema_per_type', None) or {}
    widths = {}
    for t in h.node_types:
        s = h[t]
        if t not in NT_ID:
            c['inv'].setdefault('unknown_node_types', []).append(t); continue
        names = list(s.node_name)
        c['nodes'][t] = [node_key(t, n) for n in names]; c['raw_names'][t] = names
        x = _np(s.x).astype(np.float64)
        cols = list(xs.get(t, ['graph_id'] + DOC_COLS[t]))
        c['x'][t] = {col: x[:, j] for j, col in enumerate(cols) if j < x.shape[1]}
        widths[t] = dict(x=int(x.shape[1]))
        for attr in ('y', 'y_raw'):
            if attr in s:
                y = _np(s[attr]).astype(np.float64)
                c[attr][t] = {sl: y[:, j] for j, sl in enumerate(SLOTS)}
                widths[t][attr] = int(y.shape[1])
    c['inv']['widths'] = widths
    edges, rc = [], []
    for (st, rel, dt) in h.edge_types:
        s = h[(st, rel, dt)]
        ei = _np(s.edge_index)
        na, nb = c['nodes'].get(st), c['nodes'].get(dt)
        c['relations'].append((st, rel, dt, int(ei.shape[1])))
        if na is None or nb is None:
            continue
        if rel.startswith('rc_'):
            ry = _np(getattr(s, 'rc_edge_y', None)); ryr = _np(getattr(s, 'rc_edge_y_raw', None))
            for k in range(ei.shape[1]):
                rc.append((rel, (st, na[int(ei[0, k])]), (dt, nb[int(ei[1, k])]), tuple(ry[k]) if ry is not None else (math.nan,) * 2,
                           tuple(ryr[k]) if ryr is not None else (math.nan,) * 2))
            continue
        ea = _np(getattr(s, 'edge_attr', None)); ey = _np(getattr(s, 'edge_y', None)); eyr = _np(getattr(s, 'edge_y_raw', None))
        for k in range(ei.shape[1]):
            edges.append((rel, (st, na[int(ei[0, k])]), (dt, nb[int(ei[1, k])]),
                          tuple(ea[k]) if ea is not None else (0.0,) * 8,
                          tuple(ey[k]) if ey is not None else (math.nan,) * 5, tuple(eyr[k]) if eyr is not None else (math.nan,) * 5))
        if ey is not None:
            c['inv'].setdefault('edge_y_widths', set()).add(int(ey.shape[1]))
    c['edges'], c['rc'] = edges, rc
    c['inv']['graph_kind'] = getattr(h, 'graph_kind', None)
    return c


def load_view_v2(path, view):
    import torch
    g = torch.load(path, map_location='cpu', weights_only=False)
    if hasattr(g, 'node_types') and g.node_types:
        return _hetero_claims(g, view)
    return _homo_claims(g, view)


def load_views_v2(dataset_dir, views='bcdef'):
    """{form: {view: claims}}; form 'homo' and/or 'hetero', whichever files exist."""
    d = Path(dataset_dir); res = {}
    for v in views:
        for name in (f'{v}_graph_homo.pt', f'{v}_graph.pt'):
            p = d / name
            if p.exists():
                c = load_view_v2(p, v)
                res.setdefault(c['form'], {})[v] = c
    return res
