#!/usr/bin/env python3
"""Cross-stage net lineage check (Exp 4, lineage study).

Independent truth (no builder code): for every stage DEF, physical nets are clustered through
BACKEND-added 1-in/1-out cells (BUF/INV not present in the reference netlist) and anchored by the
reference net names, with the A3 clone rule (gt_raw.buffer_groups). Two granularities:
  group       reference = 1_2_yosys.v, merged through synthesis non-inverting buffers (= the
              dataset's fixed-buffer WL groups)
  individual  reference = 2_floorplan.def canonical nets (= the dataset's graph Net nodes)
Compared with the dataset's own mapping files (net_lineage_history.json.gz verified_group_to_stage,
net_lineage.json.gz) and with route-graph stage-lineage features.
Usage: lineage_check.py DESIGN_DIR GT.pkl DATASET_ROOT OUT.json
"""
import gzip, json, pickle, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gt_raw import parse_verilog, buffer_groups, canon  # noqa: E402
from adapter_r2g6 import NAME_CONVENTION as F  # noqa: E402

STAGES = ('2_floorplan', '3_place', '4_cts', '5_route')


def owned_sets(bg, nets):
    own = defaultdict(set)
    for n in nets:
        o = bg['route_owner'].get(n, 'UNANCHORED')
        if o not in (None, 'UNANCHORED'):
            own[o].add(n)
    unanch = [n for n in nets if n not in bg['route_owner']]
    amb = [n for n in nets if n in bg['route_owner'] and bg['route_owner'][n] is None]
    bad = set(bg.get('unresolved_groups', []))
    for root, st in bg['cluster_status'].items():
        if st == 'ambiguous':
            for m in bg['cluster_members'][root]:
                if canon(m) in bg['group_of']:
                    bad.add(bg['group_of'][canon(m)])
    return own, unanch, amb, bad


def compare_sets(dataset, mine, valid, bad, examples=4):
    cats = Counter(); ex = defaultdict(list)
    for g, ds in dataset.items():
        ms = mine.get(g, set())
        if g in bad:
            cats['excluded_unresolved_in_truth'] += 1; continue
        if ds == ms:
            c = 'exact'
        elif not ms and ds:
            c = 'truth_has_no_piece'
        elif ds < ms:
            c = 'dataset_missing_pieces'
        elif ds > ms:
            c = 'dataset_extra_pieces'
        else:
            c = 'different'
        if not valid.get(g, True) and c != 'exact':
            c += '__dataset_masked'
        cats[c] += 1
        if c != 'exact' and len(ex[c]) < examples:
            ex[c].append(dict(group=g, dataset=sorted(ds)[:6], truth=sorted(ms)[:6]))
    return dict(cats), dict(ex)


def main():
    ddir, gtp, droot, outp = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4])
    gt = pickle.load(open(gtp, 'rb')); lib = gt['lib']; design = ddir.name
    vl = parse_verilog(ddir / '1_2_yosys.v', lib)
    st = gt['stages']
    fp = st['2_floorplan']
    sig = {n: r for n, r in fp['nets'].items() if r['use'] not in ('POWER', 'GROUND')}
    # floorplan reference netlist for the individual granularity
    fconns = defaultdict(dict)
    for n, r in sig.items():
        for i, p in r['conns']:
            fconns[i][p] = n
    fvl = dict(insts={i: dict(master=fp['comps'][i]['master'], conns=c) for i, c in fconns.items() if fp['comps'][i]['master'] in lib},
               aliases=[], ports={io: v['dir'] for io, v in fp['iopins'].items() if v['use'] not in ('POWER', 'GROUND')})
    res = dict(design=design, counts={}, group={}, individual={}, edge_cases={}, structure={}, alignment={})
    hist = json.load(gzip.open(droot / 'audit' / design / 'labels/net_lineage_history.json.gz', 'rt'))
    lin = json.load(gzip.open(droot / 'audit' / design / 'labels/net_lineage.json.gz', 'rt'))
    G = {}; I = {}
    for s in STAGES:
        nets = {n: r for n, r in st[s]['nets'].items() if r['use'] not in ('POWER', 'GROUND')}
        G[s] = buffer_groups(vl, lib, nets, st[s]['comps'])
        I[s] = buffer_groups(fvl, lib, nets, st[s]['comps'], merge_buffers=False)
        own, un, amb, bad = owned_sets(I[s], nets)
        pieces = Counter({k: len(v) for k, v in own.items()})
        res['counts'][s] = dict(physical_signal_nets=len(nets), canonical_nets_with_pieces=len(own),
                                split_canonical_nets=sum(v > 1 for v in pieces.values()), max_split_degree=max(pieces.values()) if pieces else 0,
                                physical_only_nets=len(un), merged_physical_nets=len(amb),
                                canonical_nets_without_piece=len(set(sig) - set(own)))
        # group level vs dataset verified_group_to_stage
        gown, gun, gamb, gbad = owned_sets(G[s], nets)
        mine = {F(k): {F(x) for x in v} for k, v in gown.items()}
        recs = hist['verified_group_to_stage'][s]['records']
        dataset = {g: {F(x) for x in r['stage_nets']} for g, r in recs.items()}
        valid = {g: bool(r['lineage_valid']) for g, r in recs.items()}
        cats, ex = compare_sets(dataset, mine, valid, {F(x) for x in gbad})
        claimed = Counter(x for g, ds in dataset.items() for x in ds)
        res['group'][s] = dict(groups=len(dataset), categories=cats, examples=ex,
                               physical_nets_in_two_dataset_groups=sum(v > 1 for v in claimed.values()),
                               truth_owned_but_unclaimed_by_dataset=len({F(x) for v in gown.values() for x in v} - set(claimed)),
                               split_groups_truth=sum(len(v) > 1 for v in gown.values()),
                               max_group_split_truth=max((len(v) for v in gown.values()), default=0),
                               dataset_masked_groups=sum(not v for v in valid.values()))
    # individual level at route vs net_lineage.json.gz (and its CTS-stage use in route-graph features)
    own, un, amb, bad = owned_sets(I['5_route'], {n for n in st['5_route']['nets']})
    mine = {F(k): {F(x) for x in v} for k, v in own.items()}
    dataset = {k: {F(x) for x in r['stage_nets']} for k, r in lin['records'].items()}
    valid = {k: bool(r['lineage_valid']) for k, r in lin['records'].items()}
    cats, ex = compare_sets(dataset, mine, valid, {F(x) for x in bad})
    res['individual']['5_route_vs_net_lineage'] = dict(nets=len(dataset), categories=cats, examples=ex)
    import torch
    b = torch.load(droot / 'route/B' / f'{design}.pt', map_location='cpu', weights_only=False)
    p = torch.load(droot / 'placement/B' / f'{design}.pt', map_location='cpu', weights_only=False)
    sch = list(b['net'].x_schema); x = b['net'].x
    cnt = dict(zip(b['net'].names, x[:, sch.index('stage_net_count')].tolist()))
    valid_l = dict(zip(b['net'].names, x[:, sch.index('stage_lineage_valid')].tolist()))
    own4, un4, amb4, bad4 = owned_sets(I['4_cts'], {n for n in st['4_cts']['nets'] if st['4_cts']['nets'][n]['use'] not in ('POWER', 'GROUND')})
    m4 = {F(k): len(v) for k, v in own4.items()}
    c4 = Counter(); ex4 = []
    for n, c in cnt.items():
        if F(n) in {F(z) for z in bad4}:
            c4['excluded'] += 1; continue
        t = m4.get(n, 0)
        k = 'exact' if int(c) == t else ('dataset_lineage_invalid' if not valid_l[n] else 'count_differs')
        c4[k] += 1
        if k == 'count_differs' and len(ex4) < 4:
            ex4.append(dict(net=n, dataset_stage_net_count=c, truth=t))
    res['individual']['4_cts_route_graph_stage_net_count'] = dict(categories=dict(c4), examples=ex4)
    # edge cases
    clocks = {fp['iopins'][s_]['net'] for c in gt['sdc']['clocks'].values() for s_ in c['sources'] if s_ in fp['iopins']}
    res['edge_cases']['clock_nets'] = [dict(net=c, cts_pieces=len(own4.get(c, ())), route_pieces=len(own.get(c, ())),
                                            dataset_route_stage_nets=len(lin['records'].get(F(c), {}).get('stage_nets', [])),
                                            dataset_route_graph_stage_net_count=cnt.get(F(c)), dataset_group=hist['group_membership'].get(F(c)),
                                            dataset_group_route_pieces=len(hist['verified_group_to_stage']['5_route']['records'].get(hist['group_membership'].get(F(c), ''), {}).get('stage_nets', [])),
                                            dataset_group_valid=hist['verified_group_to_stage']['5_route']['records'].get(hist['group_membership'].get(F(c), ''), {}).get('lineage_valid'))
                                       for c in sorted(clocks)]
    ft = [n for n, r in st['4_cts']['nets'].items() if len(r['ios']) >= 2 and not r['conns']]
    res['edge_cases']['cts_port_feedthrough_nets'] = dict(n=len(ft), examples=ft[:3])
    res['edge_cases']['route_merged_physical_nets'] = dict(n=len(amb), examples=amb[:3])
    res['edge_cases']['route_physical_only_nets'] = dict(n=len(un), examples=un[:3],
                                                         masters_of_drivers=dict(Counter(st['5_route']['comps'][i]['master'] for n_ in un for i, p_ in st['5_route']['nets'][n_]['conns']
                                                                                     if i in st['5_route']['comps'] and p_ in (lib.get(st['5_route']['comps'][i]['master'], {}).get('outs') or [])).most_common(5)))
    removed = sorted(set(sig) - set(own))
    res['edge_cases']['canonical_nets_removed_by_route'] = dict(n=len(removed), examples=removed[:3],
        dataset_individual_valid=sum(valid.get(F(n), False) for n in removed))
    # structure: synthesis netlist vs floorplan canonical vs graphs
    syn = set(vl['insts']); fpg = set(fconns)
    res['structure'] = dict(synth_instances=len(syn), floorplan_gates=len(fpg), synth_not_in_floorplan=len(syn - fpg),
                            floorplan_not_in_synth=len(fpg - syn), examples_synth_only=sorted(syn - fpg)[:3], examples_floorplan_only=sorted(fpg - syn)[:3],
                            masters_floorplan_only=dict(Counter(fp['comps'][i]['master'] for i in fpg - syn)),
                            synth_nets=len(G['2_floorplan']['group_of']), synth_groups=len(set(G['2_floorplan']['group_of'].values())),
                            floorplan_canonical_nets=len(sig), graph_nets=b['net'].num_nodes, graph_gates=b['gate'].num_nodes,
                            graph_has_noncanonical_gates=len(set(b['gate'].names) - {F(g) for g in fpg}),
                            graph_has_tap_or_fill=sum(('TAP' in fp['comps'].get(g, {}).get('master', '') or 'FILL' in fp['comps'].get(g, {}).get('master', '')) for g in b['gate'].names))
    # cross-stage alignment (same index everywhere)
    al = {}
    for t in ('gate', 'net', 'pin', 'io_pin'):
        al[f'{t}_names_identical_order_placement_route'] = list(p[t].names) == list(b[t].names)
    for v in 'CDF':
        for s_ in ('placement', 'route'):
            g = torch.load(droot / s_ / v / f'{design}.pt', map_location='cpu', weights_only=False)
            al[f'{s_}_{v}_gate_names_equal_B'] = list(g['gate'].names) == list(b['gate'].names)
            al[f'{s_}_{v}_wl_group_mapping_equal'] = bool(torch.equal(g.wl_source_net_group, b.wl_source_net_group))
    al['wl_group_names_equal'] = list(p.wl_group_names) == list(b.wl_group_names)
    al['label_grid_id_equal'] = bool(torch.equal(p['gate'].label_grid_id, b['gate'].label_grid_id))
    al['wl_group_names_equal_history'] = sorted(b.wl_group_names) == sorted(set(hist['group_membership'].values()))
    res['alignment'] = al
    outp.write_text(json.dumps(res, indent=1))
    g5 = res['group']['5_route']['categories']
    print('LIN', design, 'route groups', g5, 'indiv', res['individual']['5_route_vs_net_lineage']['categories'],
          'cts', res['individual']['4_cts_route_graph_stage_net_count']['categories'], 'align_ok', all(al.values()))


if __name__ == '__main__':
    main()
