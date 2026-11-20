#!/usr/bin/env python3
"""Format-independent EDA ground truth for one ORFS design directory (Exp 4, V1).

INDEPENDENCE: this module imports nothing from any dataset builder. It parses raw EDA files
with its own code (DEF, LEF, Liberty, gate-level Verilog, SPEF, ORFS logs/JSON, SDC via tclsh
stubs). Dataset-specific layouts live in adapter_*.py; comparisons in match.py.

Output (pickle): a plain dict, keyed by NAME everywhere:
  meta       design, dbu, die, files
  lib        cells{master: area, leakage, seq, kind(buf/inv/other), pins{pin: dir, cap_fF, func}}
  lef        layers{name: dir, pitch_um}, macros{master: w, h, cls, pins{pin: bbox}}
  stages     {2_floorplan, 3_place, 4_cts, 5_route}: comps{name: master,status,x,y,orient},
             iopins{name: net,dir,use,x,y,layer}, nets{name: use, conns[(inst,pin)], ios[...]}
  canon      gates, nets, pins(inst,pin), ios, edges (gate_pin, pin_net, io_net) from 2_floorplan
  route      net_len_um{physical net: centreline um}, per-layer stats, anomalies
  grid       2.1 um per-layer budget field: nx, ny, congestion[ny*nx], overflow, pct, gate labels
  grt        GRT-0053 resources, GRT-0096 final congestion table
  sta        6_report.json ws/tns, result.json, 5_2_route.json wirelength
  sdc        clocks, input/output delays (tclsh-evaluated)
  synth      1_2_yosys.v instances, buffer groups (own union-find), route-net ownership
  spef       per-net total / ground / coupling cap (fF)
Usage: gt_raw.py DESIGN_DIR TECH_DIR OUT.pkl
"""
import gzip, json, math, os, pickle, re, subprocess, sys, tempfile, time
from collections import defaultdict
from pathlib import Path

import numpy as np

GRID_UM = 2.1  # LABEL_SPEC: fixed 2.1 um measurement cell
# DEF escapes ('\\[') are syntax, not identity: all names are stored de-escaped (canon); collisions counted.
ESC = defaultdict(int)


def canon(name):
    """DEF/Verilog escaped name -> canonical string (drop escape backslashes)."""
    return name.replace('\\', '')


# ----------------------------------------------------------------------------- Liberty
_TOK = re.compile(r'"(?:[^"\\]|\\.)*"|[A-Za-z_][\w.\[\]\-+!*&|^\'$]*|-?\d+\.?\d*(?:[eE][-+]?\d+)?|\\\n|[{}():;,]')


def parse_liberty(paths):
    cells = {}
    units = {}
    for path in paths:
        # v2 (2026-11-07): gf180 ships gzip'd Liberty (.lib.gz)
        text = (gzip.open(path, 'rt', errors='replace').read() if str(path).endswith('.gz')
                else Path(path).read_text(errors='replace'))
        text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S).replace('\\\n', ' ')
        m = re.search(r'capacitive_load_unit\s*\(\s*([\d.]+)\s*,\s*"?(\w+)"?\s*\)', text)
        cap_scale = 1.0
        if m:
            unit = m.group(2).lower()
            cap_scale = float(m.group(1)) * {'ff': 1.0, 'pf': 1000.0}[unit]
        lm = re.search(r'leakage_power_unit\s*:\s*"?([\d.]*)\s*(\w+)"?', text)
        units[str(path)] = dict(cap_scale_to_fF=cap_scale, leakage_unit=lm.group(0) if lm else None)
        # brace-structured scan
        stack = []  # (group, name)
        cur_cell = cur_pin = None
        i = 0
        for line in text.splitlines():
            s = line.strip()
            if not s:
                continue
            g = re.match(r'(\w+)\s*\((.*?)\)\s*\{', s)
            if g:
                kind, name = g.group(1), g.group(2).strip().strip('"')
                stack.append((kind, name))
                if kind == 'cell':
                    cur_cell = cells.setdefault(name, dict(area=None, leakage=None, seq=False, pins={}, lib=str(path)))
                elif kind == 'pin' and cur_cell is not None and len(stack) >= 2 and stack[-2][0] in ('cell', 'bus', 'bundle'):
                    cur_pin = cur_cell['pins'].setdefault(name, dict(dir=None, cap_fF=None, func=None, clock=False))
                elif kind in ('ff', 'latch', 'statetable') and cur_cell is not None:
                    cur_cell['seq'] = True
                # a one-line group "x (y) { ... }" is rare in these libs; closing handled below
                if s.endswith('}'):
                    stack.pop()
                continue
            if s.startswith('}'):
                if stack:
                    kind, _ = stack.pop()
                    if kind == 'cell':
                        cur_cell = None
                    elif kind == 'pin':
                        cur_pin = None
                continue
            a = re.match(r'(\w+)\s*:\s*"?([^";]*)"?\s*;', s)
            if not a or not stack:
                continue
            key, val = a.group(1), a.group(2).strip()
            top = stack[-1][0]
            if top == 'cell' and cur_cell is not None:
                if key == 'area':
                    cur_cell['area'] = float(val)
                elif key == 'cell_leakage_power':
                    cur_cell['leakage'] = float(val)
            elif top == 'pin' and cur_pin is not None:
                if key == 'direction':
                    cur_pin['dir'] = val.lower()
                elif key == 'capacitance':
                    cur_pin['cap_fF'] = float(val) * cap_scale
                elif key == 'function':
                    cur_pin['func'] = val
                elif key == 'clock':
                    cur_pin['clock'] = val.lower() == 'true'
    for name, c in cells.items():
        ins = [p for p, v in c['pins'].items() if v['dir'] == 'input']
        outs = [p for p, v in c['pins'].items() if v['dir'] == 'output']
        kind = 'other'
        if len(ins) == 1 and len(outs) == 1 and not c['seq']:
            f = (c['pins'][outs[0]]['func'] or '').replace(' ', '')
            if f in (ins[0], '(' + ins[0] + ')'):
                kind = 'buf'
            elif f in ('!' + ins[0], ins[0] + "'", '(!' + ins[0] + ')'):
                kind = 'inv'
        c['kind'] = kind
        c['ins'], c['outs'] = ins, outs
    return cells, units


# ----------------------------------------------------------------------------- LEF
def parse_lef(paths):
    layers, macros = {}, {}
    for path in paths:
        lines = Path(path).read_text(errors='replace').splitlines()
        i = 0
        mode = None
        cur = None
        pin = None
        layer = None
        for raw in lines:
            s = raw.split('#')[0].strip()
            if not s:
                continue
            t = s.replace(';', ' ;').split()
            # v2 (2026-11-07): skip VIA / VIARULE / SITE / NONDEFAULTRULE / PROPERTYDEFINITIONS
            # blocks, whose bodies contain `LAYER x ;` lines that are not layer definitions.
            if mode is None and t[0] in ('VIA', 'VIARULE', 'SITE', 'NONDEFAULTRULE', 'PROPERTYDEFINITIONS', 'SPACING', 'UNITS'):
                mode = 'skip'; skip_end = t[1] if t[0] not in ('PROPERTYDEFINITIONS', 'SPACING', 'UNITS') and len(t) > 1 else t[0]; continue
            if mode == 'skip':
                if t[0] == 'END' and len(t) > 1 and t[1] == skip_end:
                    mode = None
                continue
            if t[0] == 'LAYER' and mode is None and len(t) >= 2:
                mode = 'layer'; layer = dict(name=t[1], type=None, dir=None, pitch=None); continue
            if mode == 'layer':
                if t[0] == 'TYPE':
                    layer['type'] = t[1]
                elif t[0] == 'DIRECTION':
                    layer['dir'] = t[1]
                elif t[0] == 'PITCH':
                    layer['pitch'] = [float(x) for x in t[1:] if x != ';']
                elif t[0] == 'END' and len(t) > 1 and t[1] == layer['name']:
                    if layer['type'] == 'ROUTING':
                        # v2: `PITCH x y` (sky130 li1) -> the track spacing across the routing
                        # direction: y-pitch for HORIZONTAL, x-pitch for VERTICAL.
                        pv = layer['pitch'] or [None]
                        pitch = pv[1] if len(pv) > 1 and layer['dir'] == 'HORIZONTAL' else pv[0]
                        layers[layer['name']] = dict(dir=layer['dir'], pitch_um=pitch)
                    mode = None
                continue
            if t[0] == 'MACRO' and mode is None:
                mode = 'macro'; cur = dict(name=t[1], w=None, h=None, cls=None, pins={}); continue
            if mode == 'macro':
                if t[0] == 'SIZE':
                    cur['w'], cur['h'] = float(t[1]), float(t[3])
                elif t[0] == 'CLASS':
                    cur['cls'] = t[1]
                elif t[0] == 'PIN':
                    pin = t[1]; mode = 'pin'; cur['pins'][pin] = dict(rects=[], use=None, dir=None)
                elif t[0] == 'END' and len(t) > 1 and t[1] == cur['name']:
                    for p in cur['pins'].values():
                        r = p['rects']
                        p['bbox'] = (min(a[0] for a in r), min(a[1] for a in r), max(a[2] for a in r), max(a[3] for a in r)) if r else None
                    macros[cur['name']] = cur; mode = None
                continue
            if mode == 'pin':
                if t[0] == 'RECT':
                    v = [float(x) for x in t[1:5]] if t[1] != 'MASK' else [float(x) for x in t[3:7]]
                    cur['pins'][pin]['rects'].append((min(v[0], v[2]), min(v[1], v[3]), max(v[0], v[2]), max(v[1], v[3])))
                elif t[0] == 'POLYGON':
                    v = [float(x) for x in t[1:] if x not in (';', 'MASK')]
                    if t[1] == 'MASK':
                        v = v[1:]
                    # v2: a rectilinear POLYGON counts as the boxes OpenDB stores for it
                    # (maximal horizontal slabs, vertically merged), as getAvgXY averages them.
                    cur['pins'][pin]['rects'].extend(polygon_slabs(v[0::2], v[1::2]))
                elif t[0] == 'USE':
                    cur['pins'][pin]['use'] = t[1]
                elif t[0] == 'DIRECTION':
                    cur['pins'][pin]['dir'] = t[1]
                elif t[0] == 'END' and len(t) > 1 and t[1] == pin:
                    mode = 'macro'
    return layers, macros


def polygon_slabs(xs, ys):
    """Rectilinear polygon -> horizontal slab rectangles, vertically adjacent slabs with the
    same x-span merged. Own implementation (even-odd crossing of vertical edges at each band)."""
    pts = list(zip(xs, ys))
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    edges = [(a[0], min(a[1], b[1]), max(a[1], b[1])) for a, b in zip(pts, pts[1:] + pts[:1]) if a[0] == b[0] and a[1] != b[1]]
    ylev = sorted({p[1] for p in pts})
    done, live = [], {}          # live: (x1, x2) -> y_start
    for lo, hi in zip(ylev, ylev[1:]):
        m = 0.5 * (lo + hi)
        xs_ = sorted(x for x, y1, y2 in edges if y1 < m < y2)
        spans = set(zip(xs_[0::2], xs_[1::2]))
        for sp in list(live):
            if sp not in spans:
                done.append((sp[0], live.pop(sp), sp[1], lo))
        for sp in spans:
            live.setdefault(sp, lo)
    if ylev:
        for sp, y0 in live.items():
            done.append((sp[0], y0, sp[1], ylev[-1]))
    return done


def orient_point(px, py, w, h, o):
    """Cell-local point -> offset from placed origin (DEF orientation, lower-left anchoring)."""
    return {'N': (px, py), 'S': (w - px, h - py), 'FN': (w - px, py), 'FS': (px, h - py),
            'W': (h - py, px), 'E': (py, w - px), 'FW': (py, px), 'FE': (h - py, w - px)}[o]


def oriented_size(w, h, o):
    return (h, w) if o in ('W', 'E', 'FW', 'FE') else (w, h)


# ----------------------------------------------------------------------------- DEF
def _statements(path, section):
    """Yield token lists of '- ... ;' statements inside SECTION (streaming)."""
    opener = re.compile(r'^\s*' + section + r'\s+\d+\s*;')
    inside = False
    buf = []
    with open(path, errors='replace') as f:
        for line in f:
            if not inside:
                if opener.match(line):
                    inside = True
                continue
            s = line.strip()
            if s.startswith('END ' + section):
                return
            if not s:
                continue
            toks = s.split()
            buf.extend(toks)
            if toks[-1] == ';':
                yield buf
                buf = []


def def_header(path):
    dbu = die = None
    with open(path, errors='replace') as f:
        for line in f:
            if line.startswith('UNITS DISTANCE MICRONS'):
                dbu = int(line.split()[3])
            elif line.startswith('DIEAREA'):
                v = [int(x) for x in re.findall(r'-?\d+', line)]
                xs, ys = v[0::2], v[1::2]
                die = (min(xs), min(ys), max(xs), max(ys))
                npts = len(xs)
            elif line.startswith('COMPONENTS'):
                break
    return dbu, die, npts


def parse_components(path):
    comps = {}
    for t in _statements(path, 'COMPONENTS'):
        name, master = canon(t[1]), t[2]
        if name in comps:
            ESC['collisions'] += 1
        ESC['escaped'] += '\\' in t[1]
        rec = dict(master=master, status=None, x=None, y=None, orient=None)
        for k in range(3, len(t)):
            if t[k] in ('PLACED', 'FIXED', 'COVER') and t[k - 1] == '+':
                rec['status'] = t[k]
                rec['x'], rec['y'], rec['orient'] = int(t[k + 2]), int(t[k + 3]), t[k + 5]
            elif t[k] == 'UNPLACED' and t[k - 1] == '+':
                rec['status'] = 'UNPLACED'
        comps[name] = rec
    return comps


def parse_pins(path):
    pins = {}
    for t in _statements(path, 'PINS'):
        rec = dict(net=None, dir=None, use='SIGNAL', x=None, y=None, layer=None, status=None, orient=None, shape=None)
        k = 2
        while k < len(t):
            if t[k] == '+':
                key = t[k + 1]
                if key == 'NET':
                    rec['net'] = canon(t[k + 2])
                elif key == 'DIRECTION':
                    rec['dir'] = t[k + 2]
                elif key == 'USE':
                    rec['use'] = t[k + 2]
                elif key in ('PLACED', 'FIXED', 'COVER'):
                    rec['status'] = key
                    rec['x'], rec['y'], rec['orient'] = int(t[k + 3]), int(t[k + 4]), t[k + 6]
                elif key == 'LAYER' and rec['layer'] is None:
                    rec['layer'] = t[k + 2]
                    nums = [int(x) for x in t[k + 3:k + 13] if re.fullmatch(r'-?\d+', x)][:4]
                    if len(nums) == 4:
                        rec['shape'] = tuple(nums)
            k += 1
        if canon(t[1]) in pins:
            ESC['collisions'] += 1
        pins[canon(t[1])] = rec
    return pins


def parse_nets(path, with_routes=False, layers=None):
    """Return nets{name: use, conns, ios}; with_routes -> also per-net per-layer segments."""
    nets = {}
    segs = defaultdict(list) if with_routes else None  # layer -> list of (net, ax, ay, bx, by)
    anomalies = defaultdict(int)
    for t in _statements(path, 'NETS'):
        name = canon(t[1])
        if name in nets:
            ESC['collisions'] += 1
        conns, ios = [], []
        k = 2
        n = len(t)
        while k < n and t[k] == '(':
            a, b = t[k + 1], t[k + 2]
            j = k + 3
            while t[j] != ')':
                j += 1
            if a == 'PIN':
                ios.append(canon(b))
            else:
                conns.append((canon(a), b))
            k = j + 1
        use = 'SIGNAL'
        routed = False
        while k < n:
            if t[k] == '+' and k + 1 < n:
                key = t[k + 1]
                if key == 'USE':
                    use = t[k + 2]; k += 3; continue
                if key in ('ROUTED', 'FIXED', 'COVER', 'NOSHIELD'):
                    routed = True
                    if with_routes:
                        k = _route_body(t, k + 2, name, segs, anomalies, layers)
                        continue
            k += 1
        nets[name] = dict(use=use, conns=conns, ios=ios, routed=routed)
    return nets, segs, dict(anomalies)


_KEYW = {'TAPER', 'TAPERRULE', 'STYLE', 'MASK', 'VIRTUAL', 'RECT', 'NEW', '+', ';'}


def _route_body(t, k, net, segs, anomalies, layer_sets=None):
    """Parse 'layer [opts] pt pt via ... NEW layer ...' until next '+' property or ';'."""
    n = len(t)
    layer = t[k]; k += 1
    if layer_sets is not None:
        layer_sets[net].add(layer)
    last = None
    via_since_pt = False
    while k < n:
        tok = t[k]
        if tok == ';' or (tok == '+' and t[k + 1] not in ('ROUTED', 'FIXED', 'COVER', 'NOSHIELD')):
            return k
        if tok == '+':
            k += 2
            layer = t[k]; k += 1; last = None
            if layer_sets is not None:
                layer_sets[net].add(layer)
            continue
        if tok == 'NEW':
            layer = t[k + 1]; k += 2; last = None; via_since_pt = False
            if layer_sets is not None:
                layer_sets[net].add(layer)
            continue
        if tok in ('TAPER',):
            k += 1; continue
        if tok in ('TAPERRULE', 'STYLE', 'MASK'):
            k += 2; continue
        if tok == 'RECT':
            anomalies['rect_patch'] += 1
            j = k
            while t[j] != ')':
                j += 1
            k = j + 1; continue
        if tok == 'VIRTUAL':
            anomalies['virtual'] += 1
            j = k
            while t[j] != ')':
                j += 1
            k = j + 1; last = None; continue
        if tok == '(':
            j = k + 1
            vals = []
            while t[j] != ')':
                vals.append(t[j]); j += 1
            x = last[0] if vals[0] == '*' else int(vals[0])
            y = last[1] if vals[1] == '*' else int(vals[1])
            if last is not None:
                if via_since_pt:
                    anomalies['via_midpath'] += 1
                if last[0] != x and last[1] != y:
                    anomalies['non_manhattan'] += 1
                if (last[0], last[1]) != (x, y):
                    segs[layer].append((net, last[0], last[1], x, y))
            last = (x, y)
            via_since_pt = False
            k = j + 1; continue
        # via name
        via_since_pt = True
        k += 1
    return k


# ----------------------------------------------------------------------------- route geometry
def route_lengths_and_grid(route_def, dbu, die, lef_layers, grt_res):
    """Per-physical-net centreline length + 2.1 um per-layer demand/budget field."""
    nets, segs, anomalies = parse_nets(route_def, with_routes=True)
    x0, y0, x1, y1 = die
    G = int(round(GRID_UM * dbu))
    nx, ny = math.ceil((x1 - x0) / G), math.ceil((y1 - y0) / G)
    net_len = defaultdict(float)
    layer_len = {}
    D = {}
    outside = 0
    for layer, rows in segs.items():
        arr = np.array([r[1:] for r in rows], dtype=np.int64)
        names = [r[0] for r in rows]
        L = np.abs(arr[:, 2] - arr[:, 0]) + np.abs(arr[:, 3] - arr[:, 1])
        for nm, l in zip(names, L.tolist()):
            net_len[nm] += l
        layer_len[layer] = float(L.sum()) / dbu
        outside += int(((np.minimum(arr[:, 0], arr[:, 2]) < x0) | (np.maximum(arr[:, 0], arr[:, 2]) > x1) |
                        (np.minimum(arr[:, 1], arr[:, 3]) < y0) | (np.maximum(arr[:, 1], arr[:, 3]) > y1)).sum())
        dem = np.zeros(nx * ny)
        for horiz in (True, False):
            if horiz:
                m = arr[:, 1] == arr[:, 3]
                fixed = arr[m, 1] - y0; a = np.minimum(arr[m, 0], arr[m, 2]) - x0; b = np.maximum(arr[m, 0], arr[m, 2]) - x0
                nfix, nrun = ny, nx
            else:
                m = (arr[:, 0] == arr[:, 2]) & (arr[:, 1] != arr[:, 3])
                fixed = arr[m, 0] - x0; a = np.minimum(arr[m, 1], arr[m, 3]) - y0; b = np.maximum(arr[m, 1], arr[m, 3]) - y0
                nfix, nrun = nx, ny
            keep = b > a
            fixed, a, b = fixed[keep], a[keep], b[keep]
            if not len(a):
                continue
            fbin = np.minimum(fixed // G, nfix - 1)
            c = np.minimum(a // G, nrun - 1)
            cend = np.minimum((b - 1) // G, nrun - 1)
            active = np.ones(len(a), bool)
            while active.any():
                idx = np.flatnonzero(active)
                cc = c[idx]
                lo = np.maximum(a[idx], cc * G)
                hi = np.where(cc == nrun - 1, b[idx], np.minimum(b[idx], (cc + 1) * G))
                seg = np.maximum(hi - lo, 0)
                gid = (fbin[idx] * nx + cc) if horiz else (cc * nx + fbin[idx])
                np.add.at(dem, gid, seg)
                c[idx] += 1
                active[idx] = c[idx] <= cend[idx]
        D[layer] = dem / dbu
    gu = G / dbu
    util = np.zeros(nx * ny); ovf = np.zeros(nx * ny)
    budgets = {}
    for layer, info in lef_layers.items():
        r = grt_res.get(layer)
        if r is None:
            raise ValueError('GRT-0053 missing layer ' + layer)
        orig, adj = r['original'], r['adjusted']
        active = orig > 0 and adj > 0
        cap = gu * gu / info['pitch_um'] * (adj / orig if orig else 0.0)
        budgets[layer] = dict(active=active, cap_um=cap, length_um=layer_len.get(layer, 0.0))
        if active and layer in D:
            util = np.maximum(util, D[layer] / cap)
            ovf += np.maximum(D[layer] - cap, 0.0)
    pct = 100.0 * float((util > 1).sum()) / (nx * ny)
    return dict(net_len_um={k: v / dbu for k, v in net_len.items()}, nets=nets, anomalies=anomalies,
                outside_die_segments=outside, layer_len_um=layer_len,
                total_len_um=sum(layer_len.values()), grid=dict(G_dbu=G, nx=nx, ny=ny, congestion=util,
                overflow=ovf, pct=pct, budgets=budgets, demand_total_um=float(sum(d.sum() for d in D.values()))))


def gate_grid_labels(comps, names, die, grid):
    x0, y0, x1, y1 = die
    G, nx, ny = grid['G_dbu'], grid['nx'], grid['ny']
    out = {}
    for n in names:
        c = comps.get(n)
        if c is None or c['x'] is None:
            out[n] = (math.nan, math.nan, -1); continue
        x, y = c['x'], c['y']
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            out[n] = (math.nan, math.nan, -1); continue
        ix = min((x - x0) // G, nx - 1); iy = min((y - y0) // G, ny - 1)
        g = iy * nx + ix
        out[n] = (float(grid['congestion'][g]), float(grid['overflow'][g]), int(g))
    return out


# ----------------------------------------------------------------------------- logs
def parse_grt_log(path):
    text = Path(path).read_text(errors='replace')
    blocks = text.split('Routing resources analysis:')
    res = {}
    if len(blocks) > 1:
        for name, d, a, b in re.findall(r'^(\S+)\s+(Horizontal|Vertical)\s+(\d+)\s+(\d+)\s+[-+\d.]+%\s*$', blocks[-1].split('GRT-0197')[0] if 'GRT-0197' in blocks[-1] else blocks[-1], re.M):
            if name in res:
                raise ValueError('duplicate GRT-0053 row ' + name)
            res[name] = dict(dir=d.upper(), original=int(a), adjusted=int(b))
    fin = {}
    blk = text.split('Final congestion report:')
    if len(blk) > 1:
        for name, r, dmd, use, mh, mv, tot in re.findall(r'^(\S+)\s+(\d+)\s+(\d+)\s+([\d.]+)%\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)', blk[-1], re.M):
            fin[name] = dict(resource=int(r), demand=int(dmd), usage_pct=float(use), max_h=int(mh), max_v=int(mv), total_overflow=int(tot))
    return dict(resources=res, final=fin, n_resource_tables=len(blocks) - 1)


# ----------------------------------------------------------------------------- SDC via tclsh stubs
SDC_STUB = r'''
set ::ports_in {}; set ::ports_out {}
set f [open [lindex $argv 1]]; while {[gets $f l] >= 0} { lassign [split $l "\t"] n d; if {$d eq "INPUT"} {lappend ::ports_in $n} elseif {$d eq "OUTPUT"} {lappend ::ports_out $n} else {lappend ::ports_in $n; lappend ::ports_out $n} }; close $f
set ::clocks {}
proc _flags {argsv} { set o {}; set pos {}; for {set i 0} {$i < [llength $argsv]} {incr i} { set a [lindex $argsv $i]; if {[string index $a 0] eq "-" && ![string is double -strict $a]} { if {$a in {-quiet -add -max -min -rise -fall -setup -hold -add_delay -network_latency_included -source_latency_included}} {dict set o $a 1} else {incr i; dict set o $a [lindex $argsv $i]} } else {lappend pos $a} }; return [list $o $pos] }
proc get_ports {args} { lassign [_flags $args] o pos; set r {}; foreach pat [join $pos] { foreach p [concat $::ports_in $::ports_out] { if {[string match $pat $p] && $p ni $r} {lappend r $p} } }; return $r }
proc all_inputs {args} { return $::ports_in }
proc all_outputs {args} { return $::ports_out }
proc get_name {o} { return $o }
proc get_clocks {args} { return [dict keys $::clocks] }
proc create_clock {args} { lassign [_flags $args] o pos; set nm [expr {[dict exists $o -name] ? [dict get $o -name] : [lindex [join $pos] 0]}]; dict set ::clocks $nm [list [dict get $o -period] [join $pos]]; puts "CLOCK\t$nm\t[dict get $o -period]\t[join $pos]" }
proc set_clock_uncertainty {args} { lassign [_flags $args] o pos; set k [expr {[dict exists $o -setup] ? "setup" : ([dict exists $o -hold] ? "hold" : "both")}]; puts "UNC\t$k\t[lindex $pos 0]" }
proc set_input_delay {args} { lassign [_flags $args] o pos; foreach p [join [lrange $pos 1 end]] { puts "IN\t$p\t[lindex $pos 0]" } }
proc set_output_delay {args} { lassign [_flags $args] o pos; foreach p [join [lrange $pos 1 end]] { puts "OUT\t$p\t[lindex $pos 0]" } }
foreach c {set_load set_driving_cell set_max_fanout set_max_transition set_false_path set_max_delay set_min_delay set_multicycle_path set_clock_groups set_propagated_clock set_input_transition set_timing_derate set_max_capacitance group_path set_clock_transition set_clock_latency set_dont_touch set_case_analysis current_design set_units} { proc $c {args} {} }
source [lindex $argv 0]
'''


def eval_sdc(sdc, iopins):
    with tempfile.TemporaryDirectory() as td:
        ports = Path(td) / 'ports.tsv'
        ports.write_text(''.join(f'{n}\t{v["dir"]}\n' for n, v in iopins.items() if v['use'] not in ('POWER', 'GROUND')))
        stub = Path(td) / 'stub.tcl'; stub.write_text(SDC_STUB)
        r = subprocess.run(['tclsh', str(stub), str(sdc), str(ports)], capture_output=True, text=True)
    if r.returncode:
        raise ValueError('SDC eval failed: ' + r.stderr[-500:])
    clocks, ind, outd, unc = {}, {}, {}, {}
    for line in r.stdout.splitlines():
        f = line.split('\t')
        if f[0] == 'CLOCK':
            clocks[f[1]] = dict(period=float(f[2]), sources=f[3].split())
        elif f[0] == 'IN':
            ind[f[1]] = float(f[2])
        elif f[0] == 'OUT':
            outd[f[1]] = float(f[2])
        elif f[0] == 'UNC':
            unc[f[1]] = float(f[2])
    return dict(clocks=clocks, input_delay=ind, output_delay=outd, uncertainty=unc)


# ----------------------------------------------------------------------------- Verilog netlist
def parse_verilog(path, lib):
    """Flat gate-level netlist -> instances{name: master, conns{pin: canon net}}, aliases, ports."""
    text = Path(path).read_text(errors='replace')
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    text = re.sub(r'\(\*.*?\*\)', ' ', text, flags=re.S)
    text = re.sub(r'//[^\n]*', ' ', text)
    ident = r'(?:\\\S+\s|[A-Za-z_][\w$]*)'
    insts, aliases, ports = {}, [], {}
    single_bit = {}
    mods = re.findall(r'\bmodule\b', text)
    body = text
    for m in re.finditer(r'\b(input|output|inout)\s+(\[\s*(-?\d+)\s*:\s*(-?\d+)\s*\])?\s*([^;]+);', body):
        d = m.group(1)
        for nm in m.group(5).split(','):
            nm = canon(nm.strip())
            if m.group(2):
                hi, lo = int(m.group(3)), int(m.group(4))
                for b in range(min(hi, lo), max(hi, lo) + 1):
                    ports[f'{nm}[{b}]'] = d
                if hi == lo:  # amendment A4: a [k:k] bus used bare (".A(bus)") is bit k
                    single_bit[nm] = f'{nm}[{lo}]'
            else:
                ports[nm] = d
    for m in re.finditer(r'\bassign\s+(.+?)\s*=\s*(.+?)\s*;', body, re.S):
        lhs, rhs = m.group(1).strip(), m.group(2).strip()
        if lhs.startswith('{'):
            ls = [x.strip() for x in lhs.strip('{}').split(',')]
            rs = [x.strip() for x in rhs.strip('{}').split(',')] if rhs.startswith('{') else []
            if len(ls) == len(rs):
                aliases.extend((canon(a), canon(b)) for a, b in zip(ls, rs))
            else:
                aliases.append(('__unparsed_concat__', lhs))
        else:
            aliases.append((canon(lhs), canon(rhs)))
    for m in re.finditer(r'(?m)^\s*(' + ident + r')\s+(' + ident + r')\s*\((.*?)\)\s*;', body, re.S):
        master = m.group(1).strip()
        if master in ('module', 'input', 'output', 'inout', 'wire', 'assign', 'reg'):
            continue
        if master not in lib:
            continue
        name = canon(m.group(2).strip())
        conns = {}
        for pm in re.finditer(r'\.(\w+)\s*\(\s*(.*?)\s*\)\s*(?:,|$)', m.group(3), re.S):
            nn = canon(pm.group(2).strip())
            conns[pm.group(1)] = single_bit.get(nn, nn)
        insts[name] = dict(master=master, conns=conns)
    aliases = [(single_bit.get(x, x), single_bit.get(y, y)) for x, y in aliases]
    return dict(insts=insts, aliases=aliases, ports=ports, n_modules=len(mods))


class UF:
    def __init__(self):
        self.p = {}

    def find(self, a):
        p = self.p
        p.setdefault(a, a)
        root = a
        while p[root] != root:
            root = p[root]
        while p[a] != root:
            p[a], a = root, p[a]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def is_const(n):
    return bool(re.fullmatch(r"\d*'[bhd][0-9a-fA-FxXzZ_]+|[01]", n))


def buffer_groups(vl, lib, route_nets, route_comps, merge_buffers=True):
    """Own reconstruction of synthesis non-inverting buffer signal groups and route ownership.

    Groups: union-find over synthesis nets through (i) assign aliases and (ii) synthesis
    instances whose Liberty cell is a 1-in/1-out non-inverting buffer. Route ownership: route
    DEF nets are clustered through BACKEND-added 1-in/1-out cells (BUF or INV, instance absent
    from the synthesis netlist); a cluster is owned by group g iff every synthesis-named net in
    it maps to g. Returns per-group member nets, route nets, WL, boundary endpoints, status.
    """
    uf = UF()
    syn_nets = set()
    for inst in vl['insts'].values():
        for p, n in inst['conns'].items():
            if not is_const(n):
                syn_nets.add(n)
    for p in vl['ports']:
        syn_nets.add(p)
    for a, b in vl['aliases']:
        if not is_const(a) and not is_const(b):
            uf.union(a, b); syn_nets.update((a, b))
    for n in syn_nets:
        uf.find(n)
    nbuf = 0
    for name, inst in vl['insts'].items():
        c = lib[inst['master']]
        if merge_buffers and c['kind'] == 'buf':
            i, o = inst['conns'].get(c['ins'][0]), inst['conns'].get(c['outs'][0])
            if i and o and not is_const(i) and not is_const(o):
                uf.union(i, o); nbuf += 1
    group_of = {n: uf.find(n) for n in syn_nets}
    # boundary endpoints: pins of group nets excluding internal synthesis-buffer pins, + ports
    bufinst = {nm for nm, inst in vl['insts'].items() if merge_buffers and lib[inst['master']]['kind'] == 'buf'}
    endpoints = defaultdict(int)
    for name, inst in vl['insts'].items():
        if name in bufinst:
            c = lib[inst['master']]
            i = inst['conns'].get(c['ins'][0])
            # a buffer whose input is a constant is a boundary driver of its output group
            if i is not None and is_const(i):
                o = inst['conns'].get(c['outs'][0])
                if o in group_of:
                    endpoints[group_of[o]] += 1
            continue
        for p, n in inst['conns'].items():
            if n in group_of:
                endpoints[group_of[n]] += 1
    for p in vl['ports']:
        endpoints[group_of[p]] += 1
    # route clusters through backend-added 1-in/1-out cells
    syn_inst = set(vl['insts'])
    ruf = UF()
    net_of_pin = {}
    for nm, rec in route_nets.items():
        ruf.find(nm)
        for inst, pin in rec['conns']:
            net_of_pin[(inst, pin)] = nm
    nback = 0
    for inst, comp in route_comps.items():
        if inst in syn_inst:
            continue
        c = lib.get(comp['master'])
        if c is None or c['kind'] not in ('buf', 'inv'):
            continue
        i, o = net_of_pin.get((inst, c['ins'][0])), net_of_pin.get((inst, c['outs'][0]))
        if i and o:
            ruf.union(i, o); nback += 1
    clusters = defaultdict(list)
    for nm in route_nets:
        clusters[ruf.find(nm)].append(nm)
    owner = {}
    status = {}
    for root, members in clusters.items():
        gs = {group_of[canon(m)] for m in members if canon(m) in group_of}
        if len(gs) == 1:
            g = next(iter(gs))
            for m in members:
                owner[m] = g
            status[root] = 'owned'
        elif not gs:
            status[root] = 'unanchored'
        else:
            status[root] = 'ambiguous'
            for m in members:
                owner[m] = None
    # unanchored clusters driven by a backend-added logic cell: accept a UNIQUE logic clone (a
    # synthesis-named instance with the same output function and the same multiset of input nets);
    # otherwise mark the candidate groups unresolved (excluded from the WL comparison).
    conns_of = defaultdict(dict)
    for nm, rec in route_nets.items():
        for inst, pin in rec['conns']:
            conns_of[inst][pin] = nm
    # Amendment 2026-11-07 (PROTOCOL A3): compare clone inputs by the GROUP that owns each input net
    # (a clone's input may be a backend-buffered copy of the original's input net), ignore backend
    # 1-in/1-out buffers/inverters when looking for a cluster's driver, and iterate so that buffer
    # trees driven by a resolved clone are resolved too.
    def in_key(inst):
        c = lib[route_comps[inst]['master']]
        return (tuple(sorted((c['pins'][o]['func'] or '') for o in c['outs'])),
                tuple(sorted(str(owner.get(conns_of[inst].get(p), conns_of[inst].get(p))) for p in c['ins'])))
    logic = [i for i, comp in route_comps.items() if lib.get(comp['master']) and lib[comp['master']]['outs']
             and lib[comp['master']]['kind'] not in ('buf', 'inv')]
    unresolved = set()
    nclone = 0
    for _ in range(4):
        by_sig = defaultdict(list)
        for inst in logic:
            if inst in syn_inst:
                by_sig[in_key(inst)].append(inst)
        changed = False
        for root, st in list(status.items()):
            if st != 'unanchored':
                continue
            members = set(clusters[root])
            drivers = {inst for m in members for inst, pin in route_nets[m]['conns']
                       if inst not in syn_inst and inst in route_comps and lib.get(route_comps[inst]['master'])
                       and pin in lib[route_comps[inst]['master']]['outs'] and lib[route_comps[inst]['master']]['kind'] not in ('buf', 'inv')}
            cands = set()
            for dv in drivers:
                for cand in by_sig.get(in_key(dv), []):
                    c = lib[route_comps[cand]['master']]
                    for o in c['outs']:
                        on = conns_of[cand].get(o)
                        if on is not None and owner.get(on) is not None:
                            cands.add(owner[on])
            if len(drivers) == 1 and len(cands) == 1:
                g = next(iter(cands))
                for m in members:
                    owner[m] = g
                status[root] = 'clone_owned'; nclone += 1; changed = True
        if not changed:
            break
    for root, st in list(status.items()):
        if st == 'unanchored':
            members = set(clusters[root])
            for m in members:
                for inst, pin in route_nets[m]['conns']:
                    if inst in syn_inst:
                        continue
                    # groups of the inputs of any backend driver in an unresolved cluster are suspect
                    c = lib.get(route_comps.get(inst, {}).get('master'))
                    if c and pin in c['outs']:
                        for p in c['ins']:
                            n = conns_of[inst].get(p)
                            if owner.get(n) is not None:
                                unresolved.add(owner[n])
            status[root] = 'unanchored_unresolved' if unresolved else 'unanchored'
    return dict(group_of=group_of, n_syn_buffers=nbuf, n_backend_1in1out=nback, endpoints=dict(endpoints),
                n_clone_owned=nclone, unresolved_groups=sorted(unresolved),
                route_owner=owner, cluster_status={k: v for k, v in status.items()},
                cluster_members=dict(clusters))


# ----------------------------------------------------------------------------- SPEF
def parse_spef(path):
    """Per-net total / ground / coupling capacitance in fF (units from *C_UNIT)."""
    if not Path(path).is_file():
        return None
    namemap = {}
    nets = {}
    scale = 1.0
    cur = None
    in_cap = False
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', errors='replace') as f:
        section = None
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith('*C_UNIT'):
                v, u = s.split()[1:3]
                scale = float(v) * {'PF': 1000.0, 'FF': 1.0, 'NF': 1e6}[u.upper()]
            elif s.startswith('*NAME_MAP'):
                section = 'map'
            elif section == 'map' and s.startswith('*') and s[1:2].isdigit():
                k, v = s.split(None, 1)
                namemap[k] = v
            elif s.startswith('*D_NET'):
                section = 'net'
                _, nm, tot = s.split()[:3]
                nm = namemap.get(nm, nm)
                cur = dict(total=float(tot) * scale, ground=0.0, coupling=0.0, n_coupling=0)
                nets[canon(nm)] = cur
                in_cap = False
            elif s.startswith('*CAP'):
                in_cap = True
            elif s.startswith('*RES') or s.startswith('*CONN') or s.startswith('*END'):
                in_cap = False
            elif in_cap and cur is not None:
                f_ = s.split()
                if len(f_) == 3:
                    cur['ground'] += float(f_[2]) * scale
                elif len(f_) == 4:
                    cur['coupling'] += float(f_[3]) * scale; cur['n_coupling'] += 1
    return nets


# ----------------------------------------------------------------------------- driver
def find1(d, pattern):
    hits = sorted(Path(d).glob(pattern))
    return hits[0] if len(hits) == 1 else None


def build(design_dir, tech_dir, with_spef=True):
    t0 = time.time()
    d = Path(design_dir); tech = Path(tech_dir)
    libs = sorted((tech / 'lib').glob('*.lib'))
    lib, lib_units = parse_liberty(libs)
    lefs = [tech / 'lef/NangateOpenCellLibrary.tech.lef', tech / 'lef/NangateOpenCellLibrary.macro.mod.lef']
    lef_layers, macros = parse_lef(lefs)
    out = dict(meta=dict(design=d.name, design_dir=str(d), tech=str(tech), lib_units=lib_units), lib=lib,
               lef=dict(layers=lef_layers, macros=macros))
    stages = {}
    for st in ('2_floorplan', '3_place', '4_cts', '5_route'):
        p = d / (st + '.def')
        dbu, die, npts = def_header(p)
        stages[st] = dict(dbu=dbu, die=die, die_points=npts, comps=parse_components(p), iopins=parse_pins(p))
        if st != '5_route':
            stages[st]['nets'] = parse_nets(p)[0]
    out['stages'] = stages
    fp = stages['2_floorplan']
    sig = {n: r for n, r in fp['nets'].items() if r['use'] not in ('POWER', 'GROUND')}
    gates = sorted({i for r in sig.values() for i, _ in r['conns'] if i in fp['comps']})
    ios = sorted(n for n, r in fp['iopins'].items() if r['use'] not in ('POWER', 'GROUND'))
    gp = sorted({(i, p) for r in sig.values() for i, p in r['conns'] if i in fp['comps']})
    pn = sorted((i, p, n) for n, r in sig.items() for i, p in r['conns'] if i in fp['comps'])
    ion = sorted((io, fp['iopins'][io]['net']) for io in ios)
    out['canon'] = dict(gates=gates, nets=sorted(sig), pins=gp, ios=ios, pin_net=pn, io_net=ion,
                        masters={g: fp['comps'][g]['master'] for g in gates},
                        unknown_masters=sorted({fp['comps'][g]['master'] for g in gates} - set(lib)),
                        io_net_by_conn=sorted((io, n) for n, r in sig.items() for io in r['ios']))
    grt_log = find1(d, 'logs/**/5_1_grt.log')
    grt = parse_grt_log(grt_log)
    out['grt'] = grt
    rt = stages['5_route']
    route = route_lengths_and_grid(d / '5_route.def', rt['dbu'], rt['die'], lef_layers, grt['resources'])
    rt['nets'] = route.pop('nets')
    grid = route.pop('grid')
    out['route'] = route
    grid['gate_labels'] = gate_grid_labels(rt['comps'], gates, rt['die'], grid)
    out['grid'] = grid
    rep = find1(d, 'logs/**/6_report.json'); rj = d / 'result.json'; r2 = find1(d, 'logs/**/5_2_route.json')
    g1 = find1(d, 'logs/**/5_1_grt.json')
    rep_j = json.loads(rep.read_text()) if rep else {}
    out['sta'] = dict(ws=rep_j.get('finish__timing__setup__ws'), tns=rep_j.get('finish__timing__setup__tns'),
                      result=json.loads(rj.read_text()) if rj.is_file() else None,
                      dr_wirelength=json.loads(r2.read_text()).get('detailedroute__route__wirelength') if r2 else None,
                      grt_ws=json.loads(g1.read_text()).get('globalroute__timing__setup__ws') if g1 else None)
    out['sdc'] = eval_sdc(d / 'constraint.sdc', fp['iopins'])
    vl = parse_verilog(d / '1_2_yosys.v', lib)
    out['synth'] = dict(instances={k: v['master'] for k, v in vl['insts'].items()}, ports=vl['ports'],
                        n_aliases=len(vl['aliases']), n_modules=vl['n_modules'])
    out['groups'] = buffer_groups(vl, lib, rt['nets'], rt['comps'])
    if with_spef:
        out['spef'] = parse_spef(d / '6_final.spef')
    out['meta']['name_escapes'] = dict(ESC)
    out['meta']['seconds'] = time.time() - t0
    return out


if __name__ == '__main__':
    res = build(sys.argv[1], sys.argv[2], with_spef=os.environ.get('GT_SPEF', '1') == '1')
    with open(sys.argv[3], 'wb') as f:
        pickle.dump(res, f, protocol=4)
    print('GT_OK', res['meta']['design'], round(res['meta']['seconds'], 1), 's')
