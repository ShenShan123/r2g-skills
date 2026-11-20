#!/usr/bin/env python3
"""V1: compare a dataset's claims (adapter) with format-independent ground truth (gt_raw).

Usage: v1_check.py GT.pkl DATASET_ROOT DESIGN ENCODE_MAP.csv OUT.json [--paths paths.json]
The comparison logic is dataset-neutral except `stage_defs` / column names, which follow the
six-label contract; a def-graph run will reuse the same truth + match functions.
"""
import csv, json, math, pickle, re, sys, time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from match import set_compare, value_compare, bool_compare, summarize, TOL  # noqa: E402
from gt_raw import canon, orient_point, oriented_size  # noqa: E402

STAGE_DEF = {'placement': '2_floorplan', 'route': '4_cts'}
NAN = math.nan


def load_encode(path):
    m = defaultdict(dict)
    with open(path) as f:
        for r in csv.DictReader(f):
            m[r['map_name']][r['raw_value']] = int(r['encoded_id'])
    return m


def drive(master):
    m = re.search(r'(?:^|_)X([0-9]+(?:\.[0-9]+)?)$', master.upper())
    return float(m.group(1)) if m else 0.0


def pairs(terms, drv, snk):
    """Documented D/E/F pairing (definition-from-code): driver->sink-or-nondriver, else star."""
    ordered = sorted(set(terms), key=lambda x: (x[0] != 'pin', x[1]))
    if len(ordered) < 2:
        return [(x, x) for x in ordered]
    d = [x for x in ordered if drv[x]]
    tg = [x for x in ordered if snk[x] or not drv[x]]
    res = list(dict.fromkeys((a, b) for a in d for b in tg if a != b))
    if res and {x for p in res for x in p} == set(ordered):
        return res
    anchor = d[0] if d else ordered[0]
    return [(anchor, x) for x in ordered if x != anchor]


def rename_truth(gt, f):
    """Amendment A5: re-key the ground truth with a dataset's DOCUMENTED name convention f (the six-label
    builder's canonical_name maps '/'->'.'), so one naming difference is reported once (I2-strict) instead
    of cascading into every name-joined group. Returns (renamed gt, number of canonical collisions)."""
    import copy
    g = copy.copy(gt)
    c = dict(gt['canon'])
    c['gates'] = [f(x) for x in c['gates']]; c['nets'] = [f(x) for x in c['nets']]; c['ios'] = [f(x) for x in c['ios']]
    c['pins'] = [(f(i), p) for i, p in c['pins']]
    c['pin_net'] = [(f(i), p, f(n)) for i, p, n in c['pin_net']]
    c['io_net'] = [(f(a), f(b)) for a, b in c['io_net']]
    c['masters'] = {f(k): v for k, v in c['masters'].items()}
    g['canon'] = c
    st = {}
    for k, v in gt['stages'].items():
        v2 = dict(v)
        v2['comps'] = {f(n): r for n, r in v['comps'].items()}
        v2['iopins'] = {f(n): dict(r, net=f(r['net']) if r.get('net') else r.get('net')) for n, r in v['iopins'].items()}
        if 'nets' in v:
            v2['nets'] = {f(n): dict(r, conns=[(f(i), p) for i, p in r['conns']], ios=[f(x) for x in r['ios']]) for n, r in v['nets'].items()}
        st[k] = v2
    g['stages'] = st
    gr = dict(gt['grid']); gr['gate_labels'] = {f(k): v for k, v in gt['grid']['gate_labels'].items()}; g['grid'] = gr
    q = dict(gt['groups'])
    q['group_of'] = {f(k): f(v) for k, v in q['group_of'].items()}
    q['route_owner'] = {f(k): (f(v) if v is not None else None) for k, v in q['route_owner'].items()}
    q['cluster_members'] = {k: [f(m) for m in v] for k, v in q['cluster_members'].items()}
    q['endpoints'] = {f(k): v for k, v in q['endpoints'].items()}
    q['unresolved_groups'] = [f(x) for x in q.get('unresolved_groups', [])]
    g['groups'] = q
    r = dict(gt['route']); r['net_len_um'] = {f(k): v for k, v in gt['route']['net_len_um'].items()}; g['route'] = r
    g['synth'] = dict(gt['synth'], instances={f(k): v for k, v in gt['synth']['instances'].items()})
    coll = (len(gt['canon']['nets']) - len(set(c['nets']))) + (len(gt['canon']['gates']) - len(set(c['gates'])))
    return g, coll


def check(gt, cl, enc, name_convention=None):
    G = {}
    if name_convention is not None:
        strict = (set_compare(cl['placement']['net']['names'], gt['canon']['nets']),
                  set_compare(cl['placement']['gate']['names'], gt['canon']['gates']))
        gt, coll = rename_truth(gt, name_convention)
        G['S1_name_convention_strict_DEF_names'] = dict(applicable=False, net_f1=strict[0]['f1'], gate_f1=strict[1]['f1'],
                                                        nets_renamed=strict[0]['n_claim'] - strict[0]['matched'],
                                                        collisions_under_convention=coll)
        G['S2_name_convention_collisions'] = dict(passed=coll == 0, collisions=coll)
    lib, macros = gt['lib'], gt['lef']['macros']
    can = gt['canon']
    fp = gt['stages']['2_floorplan']
    sdc = gt['sdc']
    period = min(c['period'] for c in sdc['clocks'].values()) if sdc['clocks'] else NAN
    pin_dir = {}
    for (i, p) in can['pins']:
        pin_dir[(i, p)] = (lib.get(can['masters'][i], {}).get('pins', {}).get(p) or {}).get('dir')
    io_dir = {io: fp['iopins'][io]['dir'] for io in can['ios']}
    io_net = dict(can['io_net'])
    # ---------------- identity / topology / features per stage
    for st in ('placement', 'route'):
        c = cl[st]
        pre = st[0].upper() + '.'
        G[pre + 'I1_gate_identity'] = set_compare(c['gate']['names'], can['gates'])
        G[pre + 'I2_net_identity'] = set_compare(c['net']['names'], can['nets'])
        G[pre + 'I3_pin_identity'] = set_compare(c['pin']['names'], [tuple(x) for x in can['pins']])
        G[pre + 'I4_io_identity'] = set_compare(c['io']['names'], can['ios'])
        G[pre + 'T1_gate_pin'] = set_compare(c['edge_gate_pin'], [(i, (i, p)) for i, p in can['pins']])
        G[pre + 'T1_pin_net'] = set_compare(c['edge_pin_net'], [((i, p), n) for i, p, n in can['pin_net']])
        G[pre + 'T1_io_net'] = set_compare(c['edge_io_net'], can['io_net'])
        # ---- derived views (definition-from-code pairing rule)
        drv, snk = {}, {}
        byn = defaultdict(list)
        for i, p, n in can['pin_net']:
            x = ('pin', (i, p)); byn[n].append(x)
            dr = pin_dir[(i, p)]
            drv[x] = dr in ('output', 'inout'); snk[x] = dr in ('input', 'inout')
        for io, n in can['io_net']:
            x = ('io_pin', io); byn[n].append(x)
            drv[x] = io_dir[io] in ('INPUT', 'INOUT', 'FEEDTHRU'); snk[x] = io_dir[io] in ('OUTPUT', 'INOUT', 'FEEDTHRU')
        D = defaultdict(list)
        for n in can['nets']:
            for a, b in pairs(byn[n], drv, snk):
                D[(a[0], b[0])].append((a[1], b[1], n))
        V = c['views']
        G[pre + 'T2_C_gate_net'] = set_compare(V['C_gate_net'], [(i, n) for i, p, n in can['pin_net']])
        for (sa, sb), rows in [((a, b), D[(a, b)]) for a in ('pin', 'io_pin') for b in ('pin', 'io_pin')]:
            for v in ('D', 'E'):
                G[f'{pre}T2_{v}_{sa}-{sb}'] = set_compare(V[f'{v}:{sa}|net_connection|{sb}'], rows)
            fa = 'gate' if sa == 'pin' else 'io_pin'; fb = 'gate' if sb == 'pin' else 'io_pin'
            frows = [(x[0] if sa == 'pin' else x, y[0] if sb == 'pin' else y, n) for x, y, n in rows]
            G[f'{pre}T2_F_{fa}-{fb}'] = set_compare(V[f'F:{fa}|net_connection|{fb}'], frows)
        arcs = []
        byg = defaultdict(list)
        for i, p in can['pins']:
            byg[i].append(('pin', (i, p)))
        for g in can['gates']:
            t = byg[g]
            ds = {x: snk[x] for x in t}; ss = {x: drv[x] for x in t}
            for a, b in pairs(t, ds, ss):
                arcs.append((a[1], b[1], None))
        G[pre + 'T2_E_cell_arc'] = set_compare(V['E:pin|cell_arc|pin'], arcs)
        # ---- features
        sd = gt['stages'][STAGE_DEF[st]]
        dbu = sd['dbu']; x0, y0, x1, y1 = sd['die']
        W, H = (x1 - x0) / dbu, (y1 - y0) / dbu
        gn = c['gate']['names']; gx = c['gate']['x']
        T = defaultdict(list)
        for g in gn:
            comp = sd['comps'].get(g)
            m = comp['master'] if comp else can['masters'].get(g, '?')
            L = lib.get(m, {}); lm = macros.get(m, {})
            placed = comp is not None and comp['x'] is not None
            T['cell_type_id'].append(enc['cell_type_id'].get(m.upper(), enc['cell_type_id']['UNKNOWN']))
            T['drive_strength'].append(drive(m))
            T['cell_area_um2'].append(L.get('area') or 0.0)
            T['cell_leakage_power'].append(L.get('leakage') or 0.0)
            T['is_sequential_cell'].append(float(L.get('seq', False)))
            T['is_buffer_cell'].append(float(L.get('kind') == 'buf'))
            T['is_inverter_cell'].append(float(L.get('kind') == 'inv'))
            T['placement_valid'].append(float(placed))
            if placed:
                ox, oy = comp['x'] / dbu, comp['y'] / dbu
                ow, oh = oriented_size(lm['w'], lm['h'], comp['orient'])
                T['x_um'].append(ox); T['y_um'].append(oy)
                T['center_x_um'].append(ox + ow / 2); T['center_y_um'].append(oy + oh / 2)
                T['center_x_normalized'].append((ox + ow / 2 - x0 / dbu) / W)
                T['center_y_normalized'].append((oy + oh / 2 - y0 / dbu) / H)
                T['orientation_id'].append(enc['orientation_id'].get(comp['orient'], -1))
            else:
                for k in ('x_um', 'y_um', 'center_x_um', 'center_y_um', 'center_x_normalized', 'center_y_normalized', 'orientation_id'):
                    T[k].append(NAN)
        cat = ('cell_type_id', 'drive_strength', 'is_sequential_cell', 'is_buffer_cell', 'is_inverter_cell', 'placement_valid', 'orientation_id')
        geo = ('x_um', 'y_um', 'center_x_um', 'center_y_um', 'center_x_normalized', 'center_y_normalized')
        for k in cat:
            G[f'{pre}F1_gate_{k}'] = value_compare(gn, gx[k], T[k], 'exact')
        for k in geo:
            G[f'{pre}F{"6" if st == "placement" else "2"}_gate_{k}'] = value_compare(gn, gx[k], T[k], 'coord_um_f32')
        for k in ('cell_area_um2', 'cell_leakage_power'):
            G[f'{pre}F4_gate_{k}'] = value_compare(gn, gx[k], T[k], 'liberty_f32')
        # pins
        pn = c['pin']['names']; px = c['pin']['x']
        P = defaultdict(list); inside = []
        for (i, p) in pn:
            comp = sd['comps'].get(i)
            m = comp['master'] if comp else can['masters'].get(i, '?')
            lp = lib.get(m, {}).get('pins', {}).get(p, {})
            P['pin_cap_fF'].append(lp.get('cap_fF') or 0.0)
            dr = lp.get('dir')
            P['is_driver_pin'].append(float(dr in ('output', 'inout')))
            P['is_sink_pin'].append(float(dr in ('input', 'inout')))
            P['cell_type_id'].append(enc['cell_type_id'].get(m.upper(), enc['cell_type_id']['UNKNOWN']))
            P['clock_period_ns'].append(period)
            placed = comp is not None and comp['x'] is not None
            lm = macros.get(m, {}); bb = (lm.get('pins', {}).get(p) or {}).get('bbox')
            if placed and bb:
                ox, oy = comp['x'] / dbu, comp['y'] / dbu
                o = comp['orient']
                cx, cy = orient_point((bb[0] + bb[2]) / 2, (bb[1] + bb[3]) / 2, lm['w'], lm['h'], o)
                P['pin_x_um'].append(ox + cx); P['pin_y_um'].append(oy + cy)
                ax, ay = orient_point(bb[0], bb[1], lm['w'], lm['h'], o); bx, by = orient_point(bb[2], bb[3], lm['w'], lm['h'], o)
                inside.append((ox + min(ax, bx), oy + min(ay, by), ox + max(ax, bx), oy + max(ay, by)))
            else:
                P['pin_x_um'].append(NAN); P['pin_y_um'].append(NAN); inside.append(None)
        for k in ('is_driver_pin', 'is_sink_pin', 'cell_type_id'):
            G[f'{pre}F1_pin_{k}'] = value_compare(pn, px[k], P[k], 'exact')
        G[f'{pre}F4_pin_cap_fF'] = value_compare(pn, px['pin_cap_fF'], P['pin_cap_fF'], 'liberty_f32')
        # post-pilot (definition, recorded in RESULTS.md): the per-pin period is domain-scoped, so a pin
        # carries either 0 (no domain) or the SDC period; check membership instead of equality.
        cp = np.asarray(px['clock_period_ns'], float)
        okp = (cp == 0) | (np.abs(cp - period) <= 1e-6 * period)
        G[f'{pre}F5_pin_clock_period_in_0_or_sdc'] = dict(n=len(cp), matched=int(okp.sum()), passed=bool(okp.all()),
                                                         n_nonzero=int((cp != 0).sum()))
        G[f'{pre}F{"6" if st == "placement" else "2"}_pin_xy_bbox_centre'] = value_compare(
            pn, np.r_[px['pin_x_um'], px['pin_y_um']], np.r_[P['pin_x_um'], P['pin_y_um']], 'coord_um_f32')
        # diagnostic: pin-centre agreement split by owner orientation
        orients = [(sd['comps'].get(i) or {}).get('orient') for (i, p) in pn]
        for grp in (('N', 'S'), ('FN', 'FS')):
            idx = [k for k, o in enumerate(orients) if o in grp]
            if idx:
                G[f'{pre}diag_pin_xy_orient_{"".join(grp)}'] = dict(applicable=False, **{k: v for k, v in value_compare(
                    [pn[k] for k in idx], np.r_[px['pin_x_um'][idx], px['pin_y_um'][idx]],
                    np.r_[np.array(P['pin_x_um'])[idx], np.array(P['pin_y_um'])[idx]], 'coord_um_f32').items() if k != 'passed'})
        tol = TOL['pin_in_shape_um'][0]
        ok = []
        for k, bb in enumerate(inside):
            a, b = px['pin_x_um'][k], px['pin_y_um'][k]
            if bb is None:
                ok.append(bool(np.isnan(a) and np.isnan(b)))
            else:
                ok.append(bool(bb[0] - tol <= a <= bb[2] + tol and bb[1] - tol <= b <= bb[3] + tol))
        G[f'{pre}F3_pin_inside_shape'] = dict(n=len(ok), matched=int(sum(ok)), rate=float(np.mean(ok)) if ok else 1.0, passed=all(ok))
        # nets
        nn = c['net']['names']; nx_ = c['net']['x']
        pins_by_net = defaultdict(list)
        for i, p, n in can['pin_net']:
            pins_by_net[n].append((i, p))
        ios_by_net = defaultdict(list)
        for io, n in can['io_net']:
            ios_by_net[n].append(io)
        cap = dict(zip(pn, P['pin_cap_fF']))
        Nt = defaultdict(list)
        for n in nn:
            dr = sk = 0; sc = 0.0
            for (i, p) in pins_by_net[n]:
                d_ = pin_dir[(i, p)]
                dr += d_ in ('output', 'inout'); sk += d_ in ('input', 'inout')
                if d_ in ('input', 'inout'):
                    sc += cap[(i, p)]
            for io in ios_by_net[n]:
                dr += io_dir[io] in ('INPUT', 'INOUT', 'FEEDTHRU'); sk += io_dir[io] in ('OUTPUT', 'INOUT', 'FEEDTHRU')
            Nt['pin_count'].append(len(pins_by_net[n]) + len(ios_by_net[n]))
            Nt['num_drivers'].append(dr); Nt['num_sinks'].append(sk); Nt['fanout'].append(max(0, sk))
            Nt['total_sink_cap_fF'].append(sc)
        for k in ('pin_count', 'num_drivers', 'num_sinks', 'fanout'):
            G[f'{pre}F1_net_{k}'] = value_compare(nn, nx_[k], Nt[k], 'exact')
        G[f'{pre}F4_net_total_sink_cap_fF'] = value_compare(nn, nx_['total_sink_cap_fF'], Nt['total_sink_cap_fF'], 'liberty_f32')
        # HPWL on nets whose stage-DEF net has exactly the canonical terminal set (independent selection)
        if st == 'route':
            pos = dict(zip(pn, zip(P['pin_x_um'], P['pin_y_um'])))
            sel, hp = [], []
            snets = sd['nets']
            for k, n in enumerate(nn):
                r = snets.get(n)
                if r is None or set(r['conns']) != set(pins_by_net[n]) or set(r['ios']) != set(ios_by_net[n]):
                    continue
                pts = [pos[t] for t in pins_by_net[n]]
                for io in ios_by_net[n]:
                    q = sd['iopins'].get(io, {})
                    pts.append((q['x'] / dbu, q['y'] / dbu) if q.get('x') is not None else (NAN, NAN))
                if len(pts) < 2 or any(np.isnan(a) for a, _ in pts):
                    continue
                xs, ys = [a for a, _ in pts], [b for _, b in pts]
                sel.append(k); hp.append(max(xs) - min(xs) + max(ys) - min(ys))
            G[f'{pre}F2_net_hpwl_um_unsplit'] = value_compare([nn[k] for k in sel], nx_['hpwl_um'][sel], hp, (1e-3, 1e-5))
            ns = [j for j, k in enumerate(sel) if all((sd['comps'].get(i) or {}).get('orient') in ('N', 'S') for i, _ in pins_by_net[nn[k]])]
            G[f'{pre}diag_hpwl_only_N_S_cells'] = dict(applicable=False, **{k: v for k, v in value_compare(
                [nn[sel[j]] for j in ns], nx_['hpwl_um'][[sel[j] for j in ns]], [hp[j] for j in ns], (1e-3, 1e-5)).items() if k != 'passed'})
            G[f'{pre}F2_net_hpwl_um_unsplit']['coverage_of_nets'] = len(sel) / max(1, len(nn))
        # io pins
        ion = c['io']['names']; ix = c['io']['x']
        I = defaultdict(list)
        for io in ion:
            q = sd['iopins'].get(io, {})
            placed = q.get('x') is not None
            I['pin_x_um'].append(q['x'] / dbu if placed else NAN); I['pin_y_um'].append(q['y'] / dbu if placed else NAN)
            d_ = io_dir[io]
            I['is_driver_pin'].append(float(d_ in ('INPUT', 'INOUT', 'FEEDTHRU'))); I['is_sink_pin'].append(float(d_ in ('OUTPUT', 'INOUT', 'FEEDTHRU')))
            I['input_delay_ns'].append(sdc['input_delay'].get(io, 0.0)); I['output_delay_ns'].append(sdc['output_delay'].get(io, 0.0))
            I['clock_period_ns'].append(period)
        for k in ('is_driver_pin', 'is_sink_pin'):
            G[f'{pre}F1_io_{k}'] = value_compare(ion, ix[k], I[k], 'exact')
        G[f'{pre}F{"6" if st == "placement" else "2"}_io_xy'] = value_compare(ion, np.r_[ix['pin_x_um'], ix['pin_y_um']], np.r_[I['pin_x_um'], I['pin_y_um']], 'coord_um_f32')
        for k in ('input_delay_ns', 'output_delay_ns'):
            G[f'{pre}F5_io_{k}'] = value_compare(ion, ix[k], I[k], 'sdc_f32')
        cp = np.asarray(ix['clock_period_ns'], float)
        okp = (cp == 0) | (np.abs(cp - period) <= 1e-6 * period)
        G[f'{pre}F5_io_clock_period_in_0_or_sdc'] = dict(n=len(cp), matched=int(okp.sum()), passed=bool(okp.all()), n_nonzero=int((cp != 0).sum()))
        # global u
        u = c['u']
        U = dict(num_logical_cells=len(can['gates']), num_logical_nets=len(can['nets']), num_ios=len(can['ios']),
                 die_width_um=W, die_height_um=H, die_area_um2=W * H, dbu_per_um=dbu,
                 freq_hz=1e9 / period, min_clock_period_ns=period, max_clock_period_ns=period, avg_clock_period_ns=period,
                 total_lib_pin_cap_fF=sum(P['pin_cap_fF']), avg_fanout=float(np.mean(Nt['fanout'])) if nn else 0.0)
        for k, v in U.items():
            tol = 'exact' if k.startswith('num_') or k == 'dbu_per_um' else ('coord_um_f32' if 'die' in k else 'liberty_f32')
            G[f'{pre}F_u_{k}'] = value_compare([k], [u[k]], [v], tol)
    # ---------------- labels
    lab = cl['route']
    gl = gt['grid']['gate_labels']
    gn = lab['gate']['names']
    tc = np.array([gl[g][0] for g in gn]); to = np.array([gl[g][1] for g in gn]); tg = np.array([gl[g][2] for g in gn])
    G['L2_gate_congestion'] = value_compare(gn, lab['gate']['y']['congestion'], tc, 'congestion')
    G['L2_gate_overflow_um'] = value_compare(gn, lab['gate']['y']['overflow'], to, 'overflow_um')
    G['L2_gate_mask'] = bool_compare(gn, lab['gate']['y_mask']['congestion'], np.isfinite(tc))
    G['L2_gate_grid_id'] = value_compare(gn, lab['gate']['label_grid_id'], tg, 'exact')
    if 'spatial' in lab:
        sp = lab['spatial']; nx, ny = gt['grid']['nx'], gt['grid']['ny']
        G['L2_grid_shape'] = dict(passed=(int(sp['shape'][0]) == ny and int(sp['shape'][1]) == nx), claim=[int(v) for v in sp['shape']], truth=[ny, nx])
        if G['L2_grid_shape']['passed']:
            gid = sp['index'][:, 1] * nx + sp['index'][:, 0]
            G['L2_field_congestion'] = value_compare(None, sp['y'][:, 0], gt['grid']['congestion'][gid], 'congestion')
            G['L2_field_overflow_um'] = value_compare(None, sp['y'][:, 1], gt['grid']['overflow'][gid], 'overflow_um')
    G['L3_gcell_pct'] = value_compare(['pct'], [lab['graph_y']['overbudget_bin_percentage_proxy_pct']], [gt['grid']['pct']], 'pct')
    sta = gt['sta']; res = sta['result'] or {}
    ws = sta['ws']; tns = sta['tns']
    cons = ws is not None and math.isfinite(ws) and abs(ws) < 1e20 and res.get('timing_path_constrained', True) is True
    tw = ws if cons else NAN; tt = tns if cons and tns is not None else NAN
    G['L4_wns_signed'] = value_compare(['wns'], [lab['graph_y']['wns_signed_ns']], [tw], 'timing_ns')
    G['L4_tns'] = value_compare(['tns'], [lab['graph_y']['tns_ns']], [tt], 'timing_ns')
    G['L4_timing_mask'] = dict(passed=bool(lab['graph_y_mask']['wns_signed_ns']) == cons and bool(lab['graph_y_mask']['tns_ns']) == cons,
                               constrained_truth=cons)
    # E arcs: labels of owning gate (owner from the pin NAME)
    for st in ('placement', 'route'):
        V = cl[st]['views']
        arcs = V['E:pin|cell_arc|pin']
        ey = V['E:pin|cell_arc|pin:edge_y']
        own = [a[0][0] for a in arcs]
        G[f'{st[0].upper()}.L5_E_arc_congestion'] = value_compare(own, ey[:, 0], [gl[g][0] for g in own], 'congestion')
        G[f'{st[0].upper()}.L5_E_arc_overflow'] = value_compare(own, ey[:, 1], [gl[g][1] for g in own], 'overflow_um')
        for v in ('C', 'D', 'F'):
            G[f'{st[0].upper()}.L5_{v}_gate_y'] = value_compare(None, V[f'{v}:gate_y'][:, 0], tc, 'congestion')
    # cross-stage equality
    a, b = cl['placement'], cl['route']
    same = (np.array_equal(a['gate']['y']['congestion'], b['gate']['y']['congestion'], equal_nan=True)
            and np.array_equal(a['gate']['y']['overflow'], b['gate']['y']['overflow'], equal_nan=True)
            and np.array_equal(a['wl']['y'], b['wl']['y'], equal_nan=True)
            and all((np.isnan(a['graph_y'][k]) and np.isnan(b['graph_y'][k])) or a['graph_y'][k] == b['graph_y'][k] for k in a['graph_y']))
    G['L6_cross_stage_labels_equal'] = dict(passed=bool(same))
    # ---------------- wirelength groups
    G.update(check_wl(gt, lab))
    # ---------------- context (reported only)
    fin = gt['grt']['final'].get('Total', {})
    G['N1_native_grt_context'] = dict(applicable=False, native_total_overflow=fin.get('total_overflow'),
                                      proxy_pct=gt['grid']['pct'], proxy_total_overflow_um=float(gt['grid']['overflow'].sum()))
    syn = set(gt['synth']['instances'])
    G['I5_netlist_crossref'] = dict(applicable=False, floorplan_gates_in_synth=sum(g in syn for g in can['gates']) / len(can['gates']),
                                    synth_insts_in_floorplan=len(syn & set(can['gates'])) / max(1, len(syn)),
                                    n_synth=len(syn), n_gates=len(can['gates']))
    return G


def check_wl(gt, lab):
    G = {}
    grp = gt['groups']
    group_of = grp['group_of']
    owner = grp['route_owner']
    L = gt['route']['net_len_um']
    my = defaultdict(float); owned_n = Counter(); amb = set()
    for net, l in L.items():
        g = owner.get(net, 'UNANCHORED')
        if g is None:
            continue
        my[g] += l
        owned_n[g] += 1
    for root, st in grp['cluster_status'].items():
        if st == 'ambiguous':
            for m in grp['cluster_members'][root]:
                if canon(m) in group_of:
                    amb.add(group_of[canon(m)])
    wl = lab['wl']
    names = [canon(n) for n in wl['names']]
    roots = [group_of.get(n) for n in names]
    mapped = [r is not None for r in roots]
    G['L1_group_names_map_to_synthesis'] = dict(n=len(names), matched=int(sum(mapped)), rate=float(np.mean(mapped)), passed=all(mapped))
    rc = Counter(r for r in roots if r is not None)
    missing_roots = sorted(set(group_of.values()) - set(rc))
    G['L1_group_one_to_one'] = dict(missing_synthesis_groups=missing_roots[:5], passed=all(v == 1 for v in rc.values()) and len(rc) == len({group_of[n] for n in group_of}),
                                    dataset_groups=len(names), synthesis_groups=len(set(group_of.values())),
                                    duplicates=sum(v - 1 for v in rc.values()))
    amb |= set(grp.get('unresolved_groups', []))
    sel = [k for k, r in enumerate(roots) if r is not None and wl['valid'][k] and owned_n[r] > 0 and r not in amb]
    G['L1_group_wirelength'] = value_compare([names[k] for k in sel], wl['y'][sel], [my[roots[k]] for k in sel], 'wl_um')
    # diagnostic: is each mismatch exactly explained by unanchored route clusters (resolver gap)?
    clen = {}
    for root, st_ in grp['cluster_status'].items():
        if st_.startswith('unanchored'):
            clen[root] = sum(L.get(m, 0.0) for m in grp['cluster_members'][root])
    lens = list(clen.values())
    expl = []
    for ex_k in [k for k in sel if not abs(wl['y'][k] - my[roots[k]]) <= 1e-6 + 1e-9 * abs(my[roots[k]])]:
        diff = wl['y'][ex_k] - my[roots[ex_k]]
        hit = any(abs(diff - l) < 1e-6 for l in lens) or any(abs(diff - a_ - b_) < 1e-6 for i_, a_ in enumerate(lens) for b_ in lens[i_ + 1:])
        expl.append(dict(group=names[ex_k], claim=float(wl['y'][ex_k]), truth=float(my[roots[ex_k]]), diff=float(diff), explained_by_unanchored_cluster=bool(hit)))
    G['L1_group_wirelength_mismatch_diagnosis'] = dict(applicable=False, mismatches=expl, n_unanchored_clusters=len(lens),
                                                       unanchored_len_um=float(sum(lens)))
    nvalid = int(wl['valid'].sum())
    G['L1_group_wirelength']['resolved_fraction_of_valid'] = len(sel) / max(1, nvalid)
    G['L1_group_wirelength']['n_valid_groups'] = nvalid
    ep = grp['endpoints']
    selp = [k for k, r in enumerate(roots) if r is not None]
    G['L1b_group_boundary_endpoints'] = value_compare([names[k] for k in selp], wl['endpoints'][selp], [ep.get(roots[k], 0) for k in selp], 'exact')
    tot = gt['route']['total_len_um']; dr = gt['sta']['dr_wirelength']
    G['L1c_total_vs_detailed_router'] = dict(def_total_um=tot, dr_json_um=dr, rel=abs(tot - dr) / dr if dr else None,
                                             passed=dr is not None and abs(tot - dr) <= TOL['dr_total_rel'][1] * dr)
    vs = float(np.nansum(wl['y'][wl['valid']]))
    G['L1d_valid_sum_le_total'] = dict(valid_sum_um=vs, def_total_um=tot, passed=vs <= tot * (1 + 1e-9) + 1e-6,
                                       unanchored_len_um=sum(l for n, l in L.items() if owner.get(n, 'U') == 'U' or n not in owner),
                                       ambiguous_len_um=sum(l for n, l in L.items() if n in owner and owner[n] is None))
    G['L1_anomalies'] = dict(applicable=False, **gt['route']['anomalies'], outside_die_segments=gt['route']['outside_die_segments'])
    return G


def main():
    gt = pickle.load(open(sys.argv[1], 'rb'))
    root, design, encp, out = sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]
    paths = None
    if '--paths' in sys.argv:
        raw = json.load(open(sys.argv[sys.argv.index('--paths') + 1]))
        paths = {tuple(k.split('/')): v for k, v in raw.items()}
    from adapter_r2g6 import load_graphs, claims_from_graphs
    t0 = time.time()
    cl = claims_from_graphs(load_graphs(root, design, paths))
    from adapter_r2g6 import NAME_CONVENTION
    G = check(gt, cl, load_encode(encp), NAME_CONVENTION)
    res = dict(design=design, seconds=time.time() - t0, verdict=summarize(G), groups=G)
    Path(out).write_text(json.dumps(res, indent=1, default=float))
    print('V1', design, 'PASS' if res['verdict']['passed'] else 'FAIL', res['verdict']['failed_groups'])


if __name__ == '__main__':
    main()
