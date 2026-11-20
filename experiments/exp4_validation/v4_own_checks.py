#!/usr/bin/env python3
"""V4: run the DATASET'S OWN acceptance checks on one design's (clean or corrupted) copies.

O1 manifest sha256 (analysis/dataset_manifest.json)        - release integrity
O2 graph_adapter.check_graph on all 10 graphs              - shipped v2 contract checker
O3 r2g5.validate_graph label invariants on B graphs        - shipped v1 checker run through a
   documented shim (contract string set to v1, an all-NaN dummy net.y added) because the
   released v2 graphs no longer carry the v1 net carrier it requires
O4 cross-stage shared-label equality (r2g5.build's check)
O5 cross-view group readout equality (test_group_graph_interface's pooling oracle)
Usage: v4_own_checks.py PROJECT RELEASE_ROOT DESIGN PATHS.json OUT.json
"""
import hashlib, json, sys, traceback
from copy import deepcopy
from pathlib import Path

import torch

project, rel, design, pathsf, outp = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], Path(sys.argv[5])
sys.path.insert(0, str(project / 'pipeline/scripts')); sys.path.insert(0, str(project / 'pipeline/group_wirelength_v2'))
import r2g5  # noqa
from graph_adapter import check_graph, pool_wirelength_embeddings  # noqa

paths = json.load(open(pathsf))
manifest = json.load(open(rel / 'analysis/dataset_manifest.json'))['graph_sha256']
G = {k: torch.load(p, map_location='cpu', weights_only=False) for k, p in paths.items()}
res = {}


def run(name, fn):
    try:
        fn(); res[name] = dict(passed=True)
    except Exception as e:  # noqa
        res[name] = dict(passed=False, error=(type(e).__name__ + ': ' + str(e))[:300])


def o1():
    bad = [k for k, p in paths.items() if hashlib.sha256(Path(p).read_bytes()).hexdigest() != manifest[f'{k}/{design}.pt']]
    assert not bad, f'{len(bad)} graphs differ from manifest'


def o2():
    for k, g in G.items():
        check_graph(g)


def o3():
    for st in ('placement', 'route'):
        g = deepcopy(G[f'{st}/B'])
        g.label_contract = r2g5.LABEL_CONTRACT
        n = g['net'].num_nodes
        g['net'].y = torch.full((n, 1), float('nan'), dtype=torch.float64)
        g['net'].y_valid_mask = torch.zeros((n, 1), dtype=torch.bool); g['net'].y_task_mask = torch.zeros((n, 1), dtype=torch.bool)
        g['net'].y_schema = r2g5.NET_SCHEMA
        r2g5.validate_graph(g)


def o4():
    a, b = G['placement/B'], G['route/B']
    for name in ('y', 'y_valid_mask', 'spatial_y', 'wl_group_y', 'wl_group_valid_mask'):
        assert torch.allclose(getattr(a, name).double(), getattr(b, name).double(), equal_nan=True), name
    for name in ('y', 'y_valid_mask', 'y_task_mask'):
        assert torch.allclose(a['gate'][name].double(), b['gate'][name].double(), equal_nan=True), 'gate.' + name


def o5():
    oracle = None
    for k, g in G.items():
        idx = torch.arange(len(g.wl_source_net_group), dtype=torch.float32)
        h = torch.stack((idx, idx.sin(), idx.cos(), torch.ones_like(idx)), 1)
        rep = h if 'net' in g.node_types else {r: h[g[r].source_net_id] for r in g.edge_types if 'source_net_id' in g[r]}
        pooled = pool_wirelength_embeddings(g, rep)
        if oracle is None:
            oracle = pooled
        assert torch.allclose(pooled, oracle, atol=1e-6, rtol=1e-6), k


for name, fn in (('O1_manifest_sha256', o1), ('O2_check_graph', o2), ('O3_validate_graph_shim', o3), ('O4_cross_stage_labels', o4), ('O5_cross_view_readout', o5)):
    run(name, fn)
outp.write_text(json.dumps(dict(design=design, checks=res), indent=1))
print('OWN', design, {k: v['passed'] for k, v in res.items()})
