#!/usr/bin/env python3
"""Why does the four-stage builder mask a synthesis net's route label? (diagnostic)
Usage: classify_4stage_masks.py DESIGN_DIR GT.pkl GENERATED_DIR"""
import json, pickle, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gt_raw import parse_verilog, canon, is_const  # noqa
ddir, gt, gen = Path(sys.argv[1]), pickle.load(open(sys.argv[2], 'rb')), Path(sys.argv[3])
lib = gt['lib']; vl = parse_verilog(ddir / '1_2_yosys.v', lib)
snap = json.load(open(gen / 'snapshots/base_to_route_snapshot.json'))
rec = (snap.get('net_lineage') or snap)['records']
rt = gt['stages']['5_route']
spin = {}
for n, r in rt['nets'].items():
    for i, p in r['conns']:
        spin[(i, p)] = n
syn_net_of = {(i, p): n for i, r in vl['insts'].items() for p, n in r['conns'].items()}
cats = Counter(); ex = defaultdict(list)
for net, r in rec.items():
    if r['lineage_valid']:
        continue
    why = []
    if r['anchor_coverage'] < 1:
        why.append('endpoint_missing_at_route')
    # pin permutation: an instance of this net whose stage pin->net map is a permutation of synthesis
    perm = False; merged = False
    for (i, p), n in syn_net_of.items():
        if n != net or i not in rt['comps']:
            continue
        sn = spin.get((i, p))
        if sn is None:
            continue
        ins = lib.get(rt['comps'][i]['master'], {}).get('ins', [])
        if p in ins:
            base = sorted(canon(vl['insts'][i]['conns'].get(q, '')) for q in ins)
            stage = sorted(canon(spin.get((i, q), '')) for q in ins)
            if base == stage and canon(sn) != canon(net) and canon(sn) in base:
                perm = True
    for sn in r['stage_nets']:
        owners = {syn_net_of.get((i, p)) for i, p in rt['nets'].get(sn, {}).get('conns', []) if (i, p) in syn_net_of}
        if len(owners - {None}) > 1:
            merged = True
    if perm:
        why.append('legal_pin_permutation')
    if merged and not perm:
        why.append('physical_net_carries_two_synthesis_nets')
    if r['ambiguous_component_count'] and not why:
        why.append('ambiguous_other')
    if not r['stage_nets']:
        why.append('no_stage_net')
    k = '+'.join(why) or 'other'; cats[k] += 1
    if len(ex[k]) < 3:
        ex[k].append(net)
print(json.dumps(dict(masked=sum(cats.values()), total=len(rec), categories=dict(cats), examples=dict(ex)), indent=1))
