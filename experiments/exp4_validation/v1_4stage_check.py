#!/usr/bin/env python3
"""Check def-graph's four-stage (R2G2.0) graphs: synthesis identity, cross-stage alignment,
one-to-many net lineage for the route labels, stage-cutoff positions.

Truth (independent): synthesis netlist 1_2_yosys.v parsed by gt_raw.parse_verilog; route
pieces by gt_raw.buffer_groups(merge_buffers=False): physical nets clustered through BACKEND-added
1-in/1-out BUF/INV cells (Liberty function), anchored by synthesis net names, A3 clone rule.
Usage: v1_4stage_check.py DESIGN_DIR GT.pkl GENERATED_DIR OUT.json
"""
import json, math, pickle, sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gt_raw import parse_verilog, buffer_groups, canon, is_const  # noqa: E402
from match import set_compare, value_compare, summarize  # noqa: E402
from adapter_defgraph import load_four_stage  # noqa: E402
from truth_defgraph import parse_spef_dg  # noqa: E402

STAGE_INPUT = {'floorplan': None, 'placement': '2_floorplan', 'cts': '3_place', 'route': '4_cts'}


def main():
    ddir, gt, gen, outp = Path(sys.argv[1]), pickle.load(open(sys.argv[2], 'rb')), sys.argv[3], Path(sys.argv[4])
    lib = gt['lib']; vl = parse_verilog(ddir / '1_2_yosys.v', lib)
    C = load_four_stage(gen); G = {}
    syn_g = sorted(vl['insts']); syn_pins = sorted((i, p) for i, r in vl['insts'].items() for p, n in r['conns'].items())
    syn_nets = sorted({n for r in vl['insts'].values() for n in r['conns'].values() if not is_const(n)} | set(vl['ports']))
    # def-graph's r2g2 canonical_name() documents the '/' -> '.' entity-name convention (as A5)
    F = lambda n: n.replace('/', '.')
    syn_g = [F(x) for x in syn_g]; syn_pins = [(F(i), p) for i, p in syn_pins]; syn_nets = [F(x) for x in syn_nets]
    inv = {}
    for r_ in vl['insts'].values():
        for n_ in r_['conns'].values():
            inv[F(n_)] = n_
    for n_ in vl['ports']:
        inv[F(n_)] = n_
    ref = C.get('route') or next(iter(C.values()))
    G['I_gate_vs_synthesis'] = set_compare(ref['nodes']['gate'], syn_g)
    G['I_pin_vs_synthesis'] = set_compare(ref['nodes']['pin'], syn_pins)
    G['I_net_vs_synthesis'] = set_compare(ref['nodes']['net'], syn_nets)
    G['I_iopin_vs_ports'] = set_compare(ref['nodes']['io_pin'], sorted(F(x) for x in vl['ports']))
    conn = sorted(((F(i), p), F(n)) for i, r in vl['insts'].items() for p, n in r['conns'].items() if not is_const(n))
    G['T_pin_net_vs_synthesis'] = set_compare(ref['edges'].get('pin|connects_to|net', []), conn)
    G['T_gate_pin_vs_synthesis'] = set_compare(ref['edges'].get('gate|has|pin', []), [(i, (i, p)) for i, p in syn_pins])
    # alignment across stages
    for t in ('gate', 'net', 'pin', 'io_pin'):
        G[f'A_{t}_same_order_all_stages'] = dict(passed=all(C[s]['nodes'][t] == ref['nodes'][t] for s in C), stages=sorted(C))
    for t in ('gate', 'net', 'pin', 'io_pin'):
        same = all(all(np.array_equal(C[s]['y'][t][k], ref['y'][t][k], equal_nan=True) and np.array_equal(C[s]['mask'][t][k], ref['mask'][t][k])
                       for k in ref['y'][t]) for s in C)
        G[f'A_{t}_labels_identical_all_stages'] = dict(passed=same)
    G['A_edges_identical_all_stages'] = dict(passed=all(C[s]['edges'].get(r) == ref['edges'].get(r) for s in C for r in ('gate|has|pin', 'pin|connects_to|net', 'io_pin|connects_to|net')))
    # one-to-many lineage at route
    rt = gt['stages']['5_route']
    nets = {n: r for n, r in rt['nets'].items() if r['use'] not in ('POWER', 'GROUND')}
    bg = buffer_groups(vl, lib, nets, rt['comps'], merge_buffers=False)
    pieces = defaultdict(list)
    for n in nets:
        o = bg['route_owner'].get(n)
        if o:
            pieces[o].append(n)
    bad = set(bg.get('unresolved_groups', []))
    for root, st in bg['cluster_status'].items():
        if st == 'ambiguous':
            bad |= {bg['group_of'].get(canon(m)) for m in bg['cluster_members'][root]}
    # truth-side many-to-one detection: a piece carrying an endpoint of ANOTHER synthesis net (not
    # explained by an input-pin permutation of the same instance) means two synthesis nets were
    # merged physically (e.g. a retired synthesis buffer): the individual label is ambiguous.
    syn_net_of = {(i, p): n for i, r in vl['insts'].items() for p, n in r['conns'].items()}
    st_pin = {}
    for n_, r_ in nets.items():
        for i, p in r_['conns']:
            st_pin[(i, p)] = n_

    def permuted(i):
        c = lib.get(rt['comps'].get(i, {}).get('master', ''), {})
        ins = c.get('ins', [])
        return bool(ins) and sorted(canon(vl['insts'][i]['conns'].get(q, '')) for q in ins) == sorted(canon(st_pin.get((i, q), '')) for q in ins)

    ports_c = {canon(x) for x in vl['ports']}

    def merged(n, ps):
        for q in ps:
            # Amendment A10 (2026-11-07): a piece that carries a top port OTHER than the net's own
            # (an IO feedthrough after the backend removed the synthesis buffer between them) also
            # merges two synthesis nets. Before A10 port endpoints were ignored, so these read as
            # "masked but truth-resolvable" (C00293, C02816, v2 C03894 FrameStrobe[k]).
            if any(canon(io) in ports_c and canon(io) != canon(n) for io in nets[q]['ios']):
                return True
            for i, p in nets[q]['conns']:
                m = syn_net_of.get((i, p))
                if m is not None and m != n and not (i in vl['insts'] and permuted(i)):
                    return True
        return False
    L = gt['route']['net_len_um']; sp = parse_spef_dg(ddir / '6_final.spef')['ground']
    rn = ref['nodes']['net']; wl = ref['y']['net']['routed_wirelength_um']; wm = ref['mask']['net']['routed_wirelength_um']
    cg = ref['y']['net']['ground_cap_pF']; cm = ref['mask']['net']['ground_cap_pF']
    cats = Counter(); ex = defaultdict(list); sel = []; tw = []; tc = []; selc = []
    for k, n in enumerate(rn):
        n = inv.get(n, n)
        root = bg['group_of'].get(n, n); ps = pieces.get(root, [])
        resolvable = root not in bad and bool(ps) and not merged(n, ps)
        t_wl = sum(L.get(q, 0.0) for q in ps)
        if wm[k] and resolvable:
            sel.append(k); tw.append(t_wl)
            if all(q in sp for q in ps) and cm[k]:
                selc.append(k); tc.append(sum(sp[q] for q in ps) / 1000.0)
            cats['valid_compared'] += 1
        elif wm[k]:
            cats['valid_truth_unresolved'] += 1
        elif resolvable:
            cats['masked_but_truth_resolvable'] += 1
            if len(ex['masked_but_truth_resolvable']) < 4:
                ex['masked_but_truth_resolvable'].append(dict(net=n, pieces=ps[:4], truth_um=t_wl))
        else:
            cats['masked_and_truth_unresolved'] += 1
        if wm[k] != cm[k] and len(ex['wl_cap_mask_differ']) < 6:
            ex['wl_cap_mask_differ'].append(dict(net=n, wl_valid=bool(wm[k]), cap_valid=bool(cm[k]), pieces=ps[:4], spef_has=[q in sp for q in ps[:4]]))
        cats['wl_valid_cap_masked'] += bool(wm[k] and not cm[k])
    G['L_route_wirelength_over_pieces'] = value_compare([rn[k] for k in sel], wl[sel], tw, (2e-4, 1e-6))
    G['L_route_ground_cap_over_pieces_pF'] = value_compare([rn[k] for k in selc], cg[selc], tc, (1e-8, 1e-5))
    G['N_lineage_categories'] = dict(applicable=False, categories=dict(cats), examples=dict(ex),
                                     split_nets_truth=sum(len(v) > 1 for v in pieces.values()), max_split=max((len(v) for v in pieces.values()), default=0))
    # canonical (synthesis) net terminal counts at every stage: IO feedthrough must not leak in (D-A2 class)
    tcount = Counter(n for (i, p), n in syn_net_of.items() if not is_const(n))
    for io, d in vl['ports'].items():
        tcount[io] += 1   # a port is a terminal of its own (bit) net
    for st in C:
        names = C[st]['nodes']['net']; pc = C[st]['x']['net'].get('pin_count')
        if pc is not None:
            G[f'F_{st}_net_pin_count_vs_synthesis'] = value_compare(names, pc, [tcount.get(inv.get(n, n), 0) for n in names], 'exact')
    # clock nets split by CTS: report the label status of each canonical clock net
    clk = sorted({n for n in rn if any(v['sources'] and n in [canon(x) for x in v['sources']] for v in gt['sdc']['clocks'].values())})
    G['N_clock_nets'] = dict(applicable=False, nets=[dict(net=n, truth_pieces=len(pieces.get(bg['group_of'].get(n, n), [])),
                                                          wl_valid=bool(wm[rn.index(n)]), wl=float(wl[rn.index(n)]),
                                                          truth_wl=sum(L.get(q, 0.0) for q in pieces.get(bg['group_of'].get(n, n), []))) for n in clk])
    # stage-cutoff positions: features of stage S come only from its input DEF
    for st, src in STAGE_INPUT.items():
        if st not in C:
            continue
        g = C[st]; names = g['nodes']['gate']; x = g['x']['gate']['x_um']
        if src is None or st == 'placement':
            G[f'P_{st}_std_cell_x_absent'] = dict(passed=bool(np.isnan(x).all()), finite=int(np.isfinite(x).sum()))
        else:
            comps = gt['stages'][src]['comps']; dbu = gt['stages'][src]['dbu']
            tr = [comps[n]['x'] / dbu if n in comps and comps[n]['x'] is not None else math.nan for n in names]
            G[f'P_{st}_x_um_from_{src}'] = value_compare(names, x, tr, (1e-4, 1e-6))
    outp.write_text(json.dumps(dict(design=ddir.name, verdict=summarize(G), groups=G), indent=1, default=float))
    print('4STG', ddir.name[:40], summarize(G)['failed_groups'], dict(cats))


if __name__ == '__main__':
    main()
