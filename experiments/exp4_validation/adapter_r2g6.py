"""Thin adapter: R2G six-label dataset (contract r2g6_v2) -> neutral, name-keyed claims.

Only reads the released .pt files (torch is used to unpickle them). No builder code is
imported. A def-graph adapter will expose the same claims layout.
"""
from pathlib import Path

import numpy as np
import torch

VIEWS = 'BCDEF'
# The builder documents its entity-name canonicalisation (02_extract_features.canonical_name):
# drop escape backslashes and write hierarchy separators as '.', i.e. DEF '/' -> '.'.
def NAME_CONVENTION(name):
    return name.replace('/', '.')
STAGES = ('placement', 'route')


def _np(t):
    return t.detach().cpu().numpy()


def _cols(store):
    x = _np(store.x).astype(np.float64)
    return {c: x[:, i] for i, c in enumerate(store.x_schema)}


def load_graphs(root, design, paths=None):
    """paths: optional {(stage, view): path} override (used by V4 on corrupted copies)."""
    out = {}
    for st in STAGES:
        for v in VIEWS:
            p = (paths or {}).get((st, v)) or Path(root) / st / v / f'{design}.pt'
            out[st, v] = torch.load(p, map_location='cpu', weights_only=False)
    return out


def claims_from_graphs(graphs):
    res = {}
    for st in STAGES:
        b = graphs[st, 'B']
        c = dict(stage=st, feature_cutoff=getattr(b, 'feature_cutoff', None))
        for t, key in (('gate', 'gate'), ('net', 'net'), ('pin', 'pin'), ('io_pin', 'io')):
            s = b[t]
            names = [tuple(n) if isinstance(n, (list, tuple)) else n for n in s.names]
            c[key] = dict(names=names, x=_cols(s), x_valid=_np(s.x_valid_mask))
        g = b['gate']
        y = _np(g.y)
        c['gate']['y'] = dict(congestion=y[:, 0], overflow=y[:, 1])
        c['gate']['y_mask'] = dict(congestion=_np(g.y_valid_mask)[:, 0], overflow=_np(g.y_valid_mask)[:, 1])
        c['gate']['y_task_mask'] = _np(g.y_task_mask)
        c['gate']['label_grid_id'] = _np(g.label_grid_id)
        c['gate']['y_schema'] = list(g.y_schema)
        gn, nn, pn, ion = c['gate']['names'], c['net']['names'], c['pin']['names'], c['io']['names']
        ei = lambda rel: _np(b[rel].edge_index)
        e = ei(('gate', 'has', 'pin')); c['edge_gate_pin'] = [(gn[i], pn[j]) for i, j in e.T]
        e = ei(('pin', 'connects_to', 'net')); c['edge_pin_net'] = [(pn[i], nn[j]) for i, j in e.T]
        e = ei(('io_pin', 'connects_to', 'net')); c['edge_io_net'] = [(ion[i], nn[j]) for i, j in e.T]
        c['u'] = dict(zip(b.u_schema, _np(b.u)[0].astype(np.float64)))
        gy = _np(b.y)[0]; gm = _np(b.y_valid_mask)[0]
        c['graph_y'] = dict(zip(b.y_schema, gy)); c['graph_y_mask'] = dict(zip(b.y_schema, gm))
        if 'spatial_y' in b:
            c['spatial'] = dict(index=_np(b.spatial_grid_index), y=_np(b.spatial_y), shape=_np(b.spatial_shape)[0],
                                schema=list(b.spatial_y_schema))
        c['wl'] = dict(names=list(b.wl_group_names), y=_np(b.wl_group_y)[:, 0], valid=_np(b.wl_group_valid_mask)[:, 0],
                       task=_np(b.wl_group_task_mask)[:, 0], endpoints=_np(b.wl_group_boundary_endpoint_count),
                       source_net_group=_np(b.wl_source_net_group), net_group_id=_np(b['net'].wl_group_id))
        # derived views, expressed by name
        views = {}
        cv = graphs[st, 'C']; s = cv['gate', 'pin_incidence', 'net']
        e = _np(s.edge_index); views['C_gate_net'] = [(cv['gate'].names[i], cv['net'].names[j]) for i, j in e.T]
        views['C_source_pin'] = [pn[k] for k in _np(s.source_pin_id)]
        for v in 'DEF':
            gv = graphs[st, v]
            for rel in gv.edge_types:
                s = gv[rel]
                e = _np(s.edge_index)
                sn = [tuple(x) if isinstance(x, (list, tuple)) else x for x in gv[rel[0]].names]
                dn = [tuple(x) if isinstance(x, (list, tuple)) else x for x in gv[rel[2]].names]
                net = [nn[k] for k in _np(s.source_net_id)] if 'source_net_id' in s else [None] * e.shape[1]
                gate = [gn[k] for k in _np(s.source_gate_id)] if 'source_gate_id' in s else None
                key = f'{v}:{rel[0]}|{rel[1]}|{rel[2]}'
                views[key] = [(sn[i], dn[j], n) for (i, j), n in zip(e.T, net)]
                if gate is not None:
                    views[key + ':gate'] = gate
                    ey = _np(s.edge_y)
                    views[key + ':edge_y'] = ey
                    views[key + ':edge_y_mask'] = _np(s.edge_y_valid_mask)
                if 'wl_group_id' in s:
                    views[key + ':wl_group_id'] = _np(s.wl_group_id)
            # gate labels carried on gate nodes in C/D/F views must equal B
            if 'gate' in gv.node_types and 'y' in gv['gate']:
                views[f'{v}:gate_y'] = _np(gv['gate'].y)
        views['C:gate_y'] = _np(cv['gate'].y)
        c['views'] = views
        res[st] = c
    return res
