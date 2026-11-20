#!/usr/bin/env python3
"""Negative controls for v2_defgraph_check.py: each deliberate corruption of a CLEAN R2G dataset
must fail exactly the group(s) that own the corrupted quantity. This proves the checker groups
are not vacuous.

Usage: v2_negative_controls.py TRUTH.pkl DATASET_DIR WORK_DIR OUT.json
"""
import copy, json, shutil, subprocess, sys
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent


def rc_cols(g, typ):
    return (g.rc_edge_type == typ).nonzero().view(-1)


CONTROLS = [
    # (name, file, mutate(g), expected failing group substring)
    ('homo_swap_two_net_names', 'b_graph_homo.pt',
     lambda g: g.node_name.__setitem__(slice(None), _swap_first_two(g, 1)), 'homo.b.O_net'),
    ('homo_coupling_raw_x1.01', 'b_graph_homo.pt',
     lambda g: g.rc_edge_y_raw.__setitem__((_largest_coupling(g), 1), g.rc_edge_y_raw[_largest_coupling(g), 1] * 1.01), 'homo.b.R_rc_coupling_raw'),
    ('homo_drop_one_resistance_edge', 'b_graph_homo.pt', '_drop_rc_res', 'homo.b.R_rc_resistance_edges'),
    ('homo_d_net_edge_attr_fanout+1', 'd_graph_homo.pt',
     lambda g: g.edge_attr.__setitem__(((g.edge_type == 1).nonzero().view(-1)[:2], 1), g.edge_attr[(g.edge_type == 1).nonzero().view(-1)[:2], 1] + 1), 'homo.d.E_net_attr1_fanout'),
    ('homo_c_pin_edge_timing_label', 'c_graph_homo.pt',
     lambda g: g.edge_y.__setitem__(((g.edge_type == 0).nonzero().view(-1)[:2], 3), g.edge_y[(g.edge_type == 0).nonzero().view(-1)[:2], 3] + 0.01), 'homo.c.E_pin_edge_y_timing'),
    ('homo_e_ground_cap_broadcast', 'e_graph_homo.pt',
     lambda g: g.y.__setitem__(((g.x[:, 0] == 3).nonzero().view(-1)[:1], 5), g.y[(g.x[:, 0] == 3).nonzero().view(-1)[:1], 5] + 0.01), 'homo.e.L_y_pin_ground_cap'),
    ('homo_break_interleave', 'f_graph_homo.pt',
     lambda g: setattr(g, 'edge_index', g.edge_index[:, torch.cat([torch.arange(1, g.edge_index.shape[1]), torch.tensor([0])])]), 'homo.f.S_edges_interleaved'),
    ('hetero_gate_x_um', 'b_graph.pt',
     lambda g: g['gate'].x.__setitem__((0, 4), g['gate'].x[0, 4] + 0.01), 'hetero.b.F_gate_x_um'),
    ('hetero_irdrop_on_net_rows', 'c_graph.pt',
     lambda g: g['net'].y.__setitem__((0, 1), 0.5), 'hetero.c.L_nan_y_net_irdrop'),
    ('hetero_f_drop_reverse_edge', 'f_graph.pt', '_drop_hetero_reverse', 'hetero.f.S_edges_reverse_symmetric'),
]


def _largest_coupling(g):
    # the fwd+rev columns of the largest coupling cap: a 1% error there is far above the
    # frozen tolerance (a 1% error on a 1e-4 fF cap is not, by design)
    c = rc_cols(g, 0); k = int(c[int(g.rc_edge_y_raw[c, 1].argmax())])
    return torch.tensor([k - (k % 2), k - (k % 2) + 1])


def _swap_first_two(g, ntype):
    names = list(g.node_name)
    idx = (g.x[:, 0] == ntype).nonzero().view(-1).tolist()
    names[idx[0]], names[idx[1]] = names[idx[1]], names[idx[0]]
    return names


def _drop_rc_res(g):
    c = rc_cols(g, 1)[:2]                      # one undirected edge = its fwd + rev columns
    keep = torch.ones(g.rc_edge_index.shape[1], dtype=torch.bool); keep[c] = False
    g.rc_edge_index, g.rc_edge_type = g.rc_edge_index[:, keep], g.rc_edge_type[keep]
    g.rc_edge_y, g.rc_edge_y_raw = g.rc_edge_y[keep], g.rc_edge_y_raw[keep]


def _drop_hetero_reverse(g):
    s = g[g.edge_types[0]]
    s.edge_index = s.edge_index[:, 1:]
    for a in ('edge_attr', 'edge_type', 'edge_y', 'edge_y_raw'):
        if a in s:
            setattr(s, a, getattr(s, a)[1:])


def main():
    truth, ds, work, outp = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    res = {}
    for name, fn, mut, expect in CONTROLS:
        d = work / name
        if d.exists():
            shutil.rmtree(d)
        shutil.copytree(ds, d, ignore=shutil.ignore_patterns('*.json'))
        g = torch.load(d / fn, weights_only=False)
        (globals()[mut] if isinstance(mut, str) else mut)(g)
        torch.save(g, d / fn)
        out = d / 'check.json'
        view = fn[0]
        subprocess.run([sys.executable, '-I', str(HERE / 'v2_defgraph_check.py'), truth, str(d), str(out), '--views', view],
                       capture_output=True, text=True)
        failed = json.loads(out.read_text())['verdict']['failed_groups']
        res[name] = dict(expected=expect, caught=any(expect == f or f.startswith(expect) for f in failed), failed=failed[:12])
        print(name, 'CAUGHT' if res[name]['caught'] else 'MISSED', failed[:6])
        shutil.rmtree(d)
    outp.write_text(json.dumps(dict(all_caught=all(v['caught'] for v in res.values()), controls=res), indent=1))
    print('NEG_CONTROLS', 'ALL_CAUGHT' if all(v['caught'] for v in res.values()) else 'SOME_MISSED')


if __name__ == '__main__':
    main()
