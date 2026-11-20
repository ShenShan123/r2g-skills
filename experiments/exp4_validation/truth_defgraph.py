#!/usr/bin/env python3
"""Ground truth for def-graph's b-f graph product, computed from the raw layout files only (v2).

This file imports no def-graph code and copies none. Raw parsing goes through gt_raw, our own
DEF, LEF and Liberty parsers, plus an own SPEF pass below. The DEFINITIONS (kept sets, net
typing, x slots, label transforms, folded edge rows, RC attachment) follow def-graph's docs:
graph-dataset.md, feature-extraction.md and label-extraction.md. Where the docs are silent, they
follow the public contract exp4_conversion/spec/CONTRACT.md. Every point taken from code is
listed in PROTOCOL.md section v2 as "definition-from-code".

v2 (2026-11-07):
- three platforms (platforms_v2.py);
- the folded-entity edges of views c, d, e and f;
- RC coupling and equivalent-resistance edges with the driver-pin attachment;
- DEF-name node order;
- power excluded: cell_power is still computed but is not scored, and IR drop is absent.

Usage: truth_defgraph.py <design_dir> <platform> <platform_dir> <sta_pins.tsv> OUT.pkl
  design_dir holds the routed DEF (platforms_v2 route_def) and 6_final.spef.
  sta_pins.tsv comes from sta_pins_dg.tcl: OpenSTA setup slack per instance pin, no parasitics.
"""
import math, pickle, re, sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gt_raw import (canon, parse_liberty, parse_lef, def_header, parse_components, parse_pins,  # noqa: E402
                    parse_nets, orient_point, oriented_size, _statements)
from platforms_v2 import PLATFORMS, GATE_EXCLUDE, files as plat_files  # noqa: E402

ORIENT_ID = dict(N=0, S=1, E=2, W=3, FN=4, FS=5, FE=6, FW=7)
STATUS_ID = dict(PLACED=0, FIXED=1, UNPLACED=2)
DIR_ID = dict(INPUT=0, OUTPUT=1, INOUT=2, FEEDTHRU=3)


def net_type(name, use):
    use = (use or 'SIGNAL').upper()
    if use == 'POWER':
        return 1
    if use == 'GROUND':
        return 2
    low = name.lower(); toks = [t for t in re.split(r'[^a-z0-9]+', low) if t]
    if use == 'CLOCK' or any('clk' in t or t == 'clock' for t in toks):
        return 3
    if 'reset' in low or low.startswith('rst') or any(t.startswith('rst') or 'reset' in t for t in toks):
        return 4
    if 'scan' in low or any(t.startswith('scan') or t in ('se', 'si', 'so', 'test', 'testmode') for t in toks):
        return 5
    return 0


def pin_type(pname, d, is_clock):
    P = pname.upper(); d = (d or '').lower()
    if P in ('VDD', 'VPWR', 'VCC', 'VCCA', 'VCCD'):
        return 12
    if P in ('VSS', 'VGND', 'GND', 'VSSA', 'VSSD'):
        return 13
    if d in ('inout', 'feedthru'):
        return 11
    if is_clock or 'CLK' in P or P in ('CK', 'CP', 'CLOCK'):
        return 5
    if P.startswith('RST') or 'RESET' in P or P in ('RN', 'RESETN', 'RSTN'):
        return 6
    if P.startswith('SET') or P in ('SN', 'SETN'):
        return 7
    if P in ('E', 'EN', 'OE', 'TE', 'GATE'):
        return 8
    if P.startswith('SCAN') or P in ('SE', 'SI', 'SO'):
        return 9
    if P in ('S', 'S0', 'S1', 'SEL', 'SELECT') and d != 'output':
        return 10
    if d == 'input':
        return 0 if P.startswith('A') else 1 if P.startswith('B') else 2 if P.startswith('C') else 3
    if d == 'output':
        return 4
    return 14


def gaussian_reflect(U, sigma=1.0, radius=4):
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2); k /= k.sum()
    out = U.astype(float)
    for ax in (0, 1):
        pad = [(0, 0), (0, 0)]; pad[ax] = (radius, radius)
        P = np.pad(out, pad, mode='symmetric')
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            sl = [slice(None), slice(None)]; sl[ax] = slice(i, i + out.shape[ax])
            acc += w * P[tuple(sl)]
        out = acc
    return out


# ----------------------------------------------------------------------------- SPEF (own pass)
_UNESC_COLON = re.compile(r'(?<!\\):')


def split_tok(tok):
    """SPEF node token -> (base, ':', sub) on the first UNESCAPED delimiter ':'."""
    m = _UNESC_COLON.search(tok)
    return (tok[:m.start()], ':', tok[m.end():]) if m else (tok, '', '')


def parse_spef_v2(path):
    """Own SPEF pass. Returns ground cap per net, port-node ground cap, coupling per unordered
    net pair (each capacitor counted once by its unordered node-token pair, both nodes resolved
    independently), the *CONN list per net (token, key, dir), and the *RES segments per net.
    Names are canonical (every backslash removed)."""
    namemap, sec, cur = {}, None, None
    cscale, rscale = 1.0, 1.0
    ground, port_cap = {}, defaultdict(float)
    conns = defaultdict(list); res = defaultdict(list); caps4 = []
    net_alias = set()
    for line in open(path, errors='replace'):
        s = line.strip()
        if not s:
            continue
        if s.startswith('*C_UNIT'):
            v, u = s.split()[1:3]; cscale = float(v) * {'PF': 1000.0, 'FF': 1.0, 'NF': 1e6}[u.upper()]; continue
        if s.startswith('*R_UNIT'):
            v, u = s.split()[1:3]; rscale = float(v) * {'OHM': 1.0, 'KOHM': 1e3}[u.upper()]; continue
        if s.startswith('*NAME_MAP'):
            sec = 'map'; continue
        if s.startswith('*D_NET') or s.startswith('*R_NET'):
            a = s.split()[1]; net_alias.add(a)
            cur = canon(namemap.get(a, a)); ground.setdefault(cur, 0.0); sec = None; continue
        if s.startswith('*CONN'):
            sec = 'conn'; continue
        if s.startswith('*CAP'):
            sec = 'cap'; continue
        if s.startswith('*RES'):
            sec = 'res'; continue
        if s.startswith('*END'):
            sec = None; cur = None; continue
        if sec == 'map':
            if s.startswith('*') and s[1:2].isdigit():
                k, v = s.split(None, 1); namemap[k] = v.strip(); continue
            sec = None
        if cur is None:
            continue
        f = s.split()
        if sec == 'conn' and f[0] in ('*I', '*P') and len(f) >= 3:
            tok = f[1]
            if f[0] == '*P':
                key = ('PIN', canon(namemap.get(tok, tok)))
            else:
                base, _, pin = split_tok(tok)
                key = (canon(namemap.get(base, base)), canon(pin))
            conns[cur].append((tok, key, f[2].upper()))
        elif sec == 'cap':
            if len(f) == 3:
                ground[cur] += float(f[2]) * cscale
                if ':' not in f[1]:
                    port_cap[canon(namemap.get(f[1], f[1]))] += float(f[2]) * cscale
            elif len(f) == 4:
                caps4.append((f[1], f[2], float(f[3]) * cscale))
        elif sec == 'res' and len(f) >= 4:
            res[cur].append((f[1], f[2], float(f[3]) * rscale))
    # node -> net resolution (independent of which block the capacitor was listed in)
    net_names = {canon(namemap.get(a, a)) for a in net_alias}
    pin_net, port_net = {}, {}
    for n, lst in conns.items():
        for tok, key, _ in lst:
            (port_net if key[0] == 'PIN' else pin_net)[key[1] if key[0] == 'PIN' else key] = n

    def node_net(tok):
        base, colon, sub = split_tok(tok)
        name = canon(namemap.get(base, base))
        if colon:
            if base.startswith('*'):
                return name if base in net_alias else pin_net.get((name, canon(sub)))
            return pin_net.get((name, canon(sub))) or (name if name in net_names else None)
        if name in port_net:
            return port_net[name]
        return name if name in net_names else None
    coupling, seen, unresolved = defaultdict(float), set(), 0
    for a, b, c in caps4:
        k = frozenset((a, b))
        if k in seen:
            continue
        seen.add(k)
        na, nb = node_net(a), node_net(b)
        if na is None or nb is None:
            unresolved += 1; continue
        if na != nb:
            coupling[frozenset((na, nb))] += c
    return dict(ground=ground, port_cap=dict(port_cap), coupling=dict(coupling), conns=dict(conns), res=dict(res),
                unresolved_coupling=unresolved, n_cap4=len(caps4))


def parse_spef_dg(path):
    """v1 name kept for v1_4stage_check.py (it reads ['ground'], fF)."""
    return parse_spef_v2(path)


def equiv_resistance(segs, pin_toks):
    """Effective (Kirchhoff) resistance between every pair of pin tokens joined in the net's
    *RES graph. On a tree this is the path-resistance sum. A component with a cycle or
    parallel resistors is solved with the Laplacian pseudo-inverse, and is flagged."""
    adj = defaultdict(list)
    for u, v, r in segs:
        adj[u].append((v, r)); adj[v].append((u, r))
    comp, out, cyclic = {}, {}, 0
    for start in adj:
        if start in comp:
            continue
        stack, members = [start], []
        comp[start] = start
        while stack:
            u = stack.pop(); members.append(u)
            for v, _ in adj[u]:
                if v not in comp:
                    comp[v] = start; stack.append(v)
        mset = set(members)
        n_edges = sum(len(adj[u]) for u in members) // 2
        pins = [t for t in pin_toks if t in mset]
        if len(pins) < 2:
            continue
        if n_edges == len(members) - 1:          # tree: path sums
            for i, a in enumerate(pins):
                dist = {a: 0.0}; st = [a]
                while st:
                    u = st.pop()
                    for v, r in adj[u]:
                        if v not in dist:
                            dist[v] = dist[u] + r; st.append(v)
                for b in pins[i + 1:]:
                    out[(a, b)] = dist[b]
        else:
            cyclic += 1
            idx = {u: k for k, u in enumerate(members)}
            L = np.zeros((len(members), len(members)))
            for u in members:
                for v, r in adj[u]:
                    if idx[u] < idx[v]:
                        g = 1.0 / r if r > 0 else 1e12
                        L[idx[u], idx[u]] += g; L[idx[v], idx[v]] += g; L[idx[u], idx[v]] -= g; L[idx[v], idx[u]] -= g
            Lp = np.linalg.pinv(L)
            for i, a in enumerate(pins):
                for b in pins[i + 1:]:
                    ia, ib = idx[a], idx[b]
                    out[(a, b)] = float(Lp[ia, ia] + Lp[ib, ib] - 2 * Lp[ia, ib])
    return out, cyclic


def raw_def_names(path):
    """canonical -> DEF-written (escaped) name for components, nets and pins (sort keys)."""
    raw = dict(comp={}, net={}, pin={})
    for sec, key in (('COMPONENTS', 'comp'), ('NETS', 'net'), ('PINS', 'pin')):
        for t in _statements(path, sec):
            raw[key][canon(t[1])] = t[1]
    return raw


def main():
    ddir, platform, pdir, sta_tsv, outp = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]), sys.argv[4], sys.argv[5]
    PF = plat_files(platform, pdir)
    lib, lib_units = parse_liberty(PF['libs'])
    layers, macros = parse_lef(PF['lefs'])
    route = ddir / PLATFORMS[platform]['route_def']
    dbu, die, _ = def_header(route)
    comps = parse_components(route); pins = parse_pins(route)
    raw = raw_def_names(route)
    layer_by_net = defaultdict(set)
    nets, segs, anomalies = parse_nets(route, with_routes=True, layers=layer_by_net)
    lens = defaultdict(int)
    for L, rows in segs.items():
        for n, ax, ay, bx, by in rows:
            lens[n] += abs(bx - ax) + abs(by - ay)
    types = {n: net_type(n, r['use']) for n, r in nets.items()}
    kept_pins = sorted({(i, p) for n, r in nets.items() if types[n] == 0 for i, p in r['conns'] if i in comps})
    owners = defaultdict(list)
    for i, p in kept_pins:
        owners[i].append(p)
    gates = sorted(i for i in owners if not re.search(GATE_EXCLUDE, comps[i]['master'], re.I))
    gset = set(gates)
    kept_pins = [(i, p) for i, p in kept_pins if i in gset]
    pset = set(kept_pins)
    io_type = {io: net_type(r['net'] or '', r['use']) for io, r in pins.items()}
    iopins = sorted(io for io, t in io_type.items() if t == 0)
    ioset = set(iopins)
    pin_net = {}
    for n, r in nets.items():
        if types[n] == 0:
            for i, p in r['conns']:
                pin_net[(i, p)] = n
    kept_nets = sorted(n for n, r in nets.items() if types[n] == 0 and (any((i, p) in pset for i, p in r['conns']) or any(io in ioset for io in r['ios'])))
    nset = set(kept_nets)
    rk = lambda d, k: raw[d].get(k, k)
    order = dict(gate=sorted(gates, key=lambda g: rk('comp', g)), net=sorted(kept_nets, key=lambda n: rk('net', n)),
                 iopin=sorted(iopins, key=lambda x: rk('pin', x)),
                 pin=sorted(kept_pins, key=lambda ip: (rk('comp', ip[0]), ip[1])))
    T = dict(design=ddir.name, platform=platform, gates=gates, nets=kept_nets, iopins=iopins, pins=kept_pins,
             order=order, anomalies=dict(anomalies), lib_units=lib_units)
    # ---- topology per view, as {family: [(endA, endB, entity)]}  (endpoint = (type, key))
    G_, N_, I_, P_ = (lambda x: ('gate', x)), (lambda x: ('net', x)), (lambda x: ('iopin', x)), (lambda x: ('pin', tuple(x)))
    io_net = [(io, pins[io]['net']) for io in iopins if pins[io]['net'] in nset]
    by_net_pins = defaultdict(list); by_net_ios = defaultdict(list)
    for i, p in kept_pins:
        by_net_pins[pin_net[(i, p)]].append((i, p))
    for io, n in io_net:
        by_net_ios[n].append(io)

    def clique(ends, ent):
        e = sorted(set(ends), key=repr)
        return [(e[a], e[b], ent) for a in range(len(e)) for b in range(a + 1, len(e))]
    V = {}
    V['b'] = {'connects': [(G_(i), P_((i, p)), None) for i, p in kept_pins] + [(P_((i, p)), N_(pin_net[(i, p)]), None) for i, p in kept_pins]
              + [(I_(io), N_(n), None) for io, n in io_net]}
    V['c'] = {'pin': [(G_(i), N_(pin_net[(i, p)]), (i, p)) for i, p in kept_pins], 'iopin_connection': [(I_(io), N_(n), None) for io, n in io_net]}
    net_cl_pin = [e for n in kept_nets for e in clique([P_(x) for x in by_net_pins[n]] + [I_(x) for x in by_net_ios[n]], n)]
    V['d'] = {'gate_pin': [(G_(i), P_((i, p)), None) for i, p in kept_pins], 'net': net_cl_pin}
    V['e'] = {'gate': [e for g in gates for e in clique([P_((g, p)) for p in owners[g]], g)], 'net': net_cl_pin}
    V['f'] = {'net': [e for n in kept_nets for e in clique([G_(x[0]) for x in by_net_pins[n]] + [I_(x) for x in by_net_ios[n]], n)]}
    T['views'] = V
    # ---- gate features
    names = sorted(c.upper() for c in lib)
    ctid = {c: k for k, c in enumerate(names)}
    Gx = {}
    for g in gates:
        c = comps[g]; Lb = lib.get(c['master'], {})
        Gx[g] = dict(cell_type_id=ctid.get(c['master'].upper(), len(names)), cell_area=Lb.get('area') or 0.0,
                     cell_power=Lb.get('leakage') or 0.0, x_um=c['x'] / dbu if c['x'] is not None else math.nan,
                     y_um=c['y'] / dbu if c['y'] is not None else math.nan, orientation_id=ORIENT_ID.get(c['orient'], -1),
                     placement_status_id=STATUS_ID.get(c['status'], -1))
    T['gate_x'] = Gx

    def pin_pt(i, p):
        c = comps[i]; m = macros.get(c['master'])
        if c['x'] is None:
            return None
        if not m or p not in m['pins'] or not m['pins'][p]['rects']:
            return (c['x'] / dbu, c['y'] / dbu)
        r = m['pins'][p]['rects']
        cx = sum((a[0] + a[2]) / 2 for a in r) / len(r); cy = sum((a[1] + a[3]) / 2 for a in r) / len(r)
        ox, oy = orient_point(cx, cy, m['w'], m['h'], c['orient'])
        return (c['x'] / dbu + ox, c['y'] / dbu + oy)
    Nf = {}
    route_layers = {l.lower() for l in layers}
    for n in kept_nets:
        r = nets[n]; dr = sk = 0; pts = []
        for i, p in r['conns']:
            dd = (lib.get(comps[i]['master'], {}).get('pins', {}).get(p) or {}).get('dir') if i in comps else None
            dr += dd in ('output', 'inout'); sk += dd in ('input', 'inout')
            q = pin_pt(i, p) if i in comps else None
            if q:
                pts.append(q)
        for io in r['ios']:
            q = pins.get(io, {}); di = (q.get('dir') or '').upper()
            dr += di in ('INPUT', 'INOUT', 'FEEDTHRU'); sk += di in ('OUTPUT', 'INOUT', 'FEEDTHRU')
            if q.get('x') is not None:
                pts.append((q['x'] / dbu, q['y'] / dbu))
        pc = len(r['conns']) + len(r['ios'])
        hp = (max(a for a, _ in pts) - min(a for a, _ in pts) + max(b for _, b in pts) - min(b for _, b in pts)) if pts else 0.0
        Nf[n] = dict(net_type_id=0, fanout=max(0, pc - 1), pin_count=pc, num_drivers=dr, num_sinks=sk, connects_macro_flag=0,
                     num_layer=len({l.lower() for l in layer_by_net.get(n, ())} & route_layers), hpwl_um=hp)
    T['net_x'] = Nf
    tap = PF['tap']
    taps = [(c['x'] / dbu, c['y'] / dbu) for c in comps.values() if any(t in c['master'].upper() for t in tap) and c['x'] is not None]
    Ix = {}
    for io in iopins:
        q = pins[io]; x, y = (q['x'] / dbu, q['y'] / dbu) if q['x'] is not None else (math.nan, math.nan)
        Ix[io] = dict(pin_x_um=x, pin_y_um=y, nearest_tap_distance_um=min((math.hypot(x - a, y - b) for a, b in taps), default=0.0),
                      pin_direction_id=DIR_ID.get((q['dir'] or '').upper(), -1))
    T['iopin_x'] = Ix
    T['n_taps'] = len(taps)
    sp = parse_spef_v2(ddir / '6_final.spef')
    netcap = {}
    for n in kept_nets:
        s = sum((lib.get(comps[i]['master'], {}).get('pins', {}).get(p) or {}).get('cap_fF') or 0.0 for i, p in nets[n]['conns'] if i in comps)
        s += sum(sp['port_cap'].get(io, 0.0) for io in nets[n]['ios'])
        netcap[n] = s
    Px = {}
    for i, p in kept_pins:
        lp = lib.get(comps[i]['master'], {}).get('pins', {}).get(p) or {}
        Px[(i, p)] = dict(pin_type_id=pin_type(p, lp.get('dir'), lp.get('clock')), sum_pin_cap_fF=netcap[pin_net[(i, p)]])
    T['pin_x'] = Px
    T['pin_net'] = {k: pin_net[k] for k in kept_pins}
    # ---- labels
    T['wirelength_um'] = {n: lens.get(n, 0) / dbu for n in kept_nets}
    T['ground_cap_fF'] = {n: sp['ground'].get(n, math.nan) for n in kept_nets}
    sx = sy = None
    for line in open(route, errors='replace'):
        if line.startswith('GCELLGRID X') and sx is None:
            sx = int(line.split()[6])
        elif line.startswith('GCELLGRID Y') and sy is None:
            sy = int(line.split()[6])
        elif line.startswith('COMPONENTS'):
            break
    x0, y0, x1, y1 = die
    nx, ny = math.ceil((x1 - x0) / sx), math.ceil((y1 - y0) / sy)
    Dh = np.zeros((nx, ny)); Dv = np.zeros((nx, ny))
    for L, rows in segs.items():
        for n, ax, ay, bx, by in rows:
            if ay == by and ax != bx:
                a, b = sorted((ax, bx)); j = (ay - y0) // sy
                if not 0 <= j < ny:
                    continue
                for i in range(max(0, (a - x0) // sx), min(nx - 1, (b - x0) // sx) + 1):
                    ov = min(b, x0 + (i + 1) * sx) - max(a, x0 + i * sx)
                    if ov > 0:
                        Dh[i, j] += ov / dbu
            elif ax == bx and ay != by:
                a, b = sorted((ay, by)); i = (ax - x0) // sx
                if not 0 <= i < nx:
                    continue
                for j in range(max(0, (a - y0) // sy), min(ny - 1, (b - y0) // sy) + 1):
                    ov = min(b, y0 + (j + 1) * sy) - max(a, y0 + j * sy)
                    if ov > 0:
                        Dv[i, j] += ov / dbu
    gw, gh = sx / dbu, sy / dbu
    Ch = sum(gw * gh / v['pitch_um'] for v in layers.values() if v['dir'] == 'HORIZONTAL')
    Cv = sum(gw * gh / v['pitch_um'] for v in layers.values() if v['dir'] == 'VERTICAL')
    U = np.maximum(Dh / Ch, Dv / Cv); Gs = gaussian_reflect(U)
    cong = {}
    for g in gates:
        c = comps[g]; m = macros.get(c['master'])
        if c['x'] is None or not m:
            cong[g] = (math.nan, math.nan, math.nan); continue
        w, h = oriented_size(m['w'], m['h'], c['orient'])
        xl, yl = c['x'], c['y']; xh, yh = c['x'] + round(w * dbu), c['y'] + round(h * dbu)
        i0, i1 = min(max((xl - x0) // sx, 0), nx - 1), min(max((xh - x0) // sx, 0), nx - 1)
        j0, j1 = min(max((yl - y0) // sy, 0), ny - 1), min(max((yh - y0) // sy, 0), ny - 1)
        blk = Gs[i0:i1 + 1, j0:j1 + 1]; ublk = U[i0:i1 + 1, j0:j1 + 1]
        cong[g] = (float(blk.mean()), float(np.sqrt(blk).mean()), float(np.sqrt(ublk).mean()))
    T['congestion'] = cong
    T['grid'] = dict(sx=sx, sy=sy, nx=nx, ny=ny, U=U, G=Gs)
    period = None; slack = {}
    for line in open(sta_tsv):
        f = line.rstrip('\n').split('\t')
        if f[0] == 'PERIOD':
            period = float(f[1]); continue
        try:
            s = float(f[2])
        except (ValueError, IndexError):
            continue
        if abs(s) < 1e29:
            k = canon(f[0]); slack[k] = min(slack.get(k, s), s)
    T['period'] = period
    T['path_delay'] = {i: (max(0.0, period - slack[i]) if i in slack else 0.0) for i in gates}
    # ---- RC parasitic edges (labels): coupling net pairs + same-net pin-pair equivalent resistance
    T['rc_coupling_nets'] = {tuple(sorted(k)): v for k, v in sp['coupling'].items()}
    res_pairs, n_cyclic = {}, 0
    for n, lst in sp['conns'].items():
        tok2key = {tok: key for tok, key, _ in lst}
        er, cyc = equiv_resistance(sp['res'].get(n, []), list(tok2key))
        n_cyclic += cyc
        for (a, b), ohm in er.items():
            ka, kb = tok2key[a], tok2key[b]
            if ka != kb:
                res_pairs[tuple(sorted((ka, kb)))] = ohm
    T['rc_res_pairs'] = res_pairs
    T['rc_cyclic_components'] = n_cyclic
    # driver: first instance pin listed with direction O in *CONN, else the first port with
    # direction I, else the first connection (label-extraction.md "Driver detection")
    drv = {}
    for n, lst in sp['conns'].items():
        d = next((k for _, k, di in lst if k[0] != 'PIN' and di == 'O'), None) or \
            next((k for _, k, di in lst if k[0] == 'PIN' and di == 'I'), None) or (lst[0][1] if lst else None)
        drv[n] = d
    T['rc_driver'] = drv
    T['spef_stats'] = dict(unresolved_coupling=sp['unresolved_coupling'], n_cap4=sp['n_cap4'], n_dnet=len(sp['ground']),
                           n_coupling_pairs=len(sp['coupling']), n_res_pairs=len(res_pairs), cyclic=n_cyclic)
    with open(outp, 'wb') as fh:
        pickle.dump(T, fh, protocol=4)
    print('DGTRUTH', ddir.name, platform, len(gates), len(kept_nets), len(iopins), len(kept_pins), 'grid', nx, ny,
          'rc', T['spef_stats'])


if __name__ == '__main__':
    main()
