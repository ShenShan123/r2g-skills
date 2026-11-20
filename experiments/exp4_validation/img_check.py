#!/usr/bin/env python3
"""V1 for the CNN image dataset: re-rasterise X and the label independently and compare.

Inputs: DESIGN_DIR TECH_DIR TRUTH_DIR(gr_truth.json, power_truth.tsv from img_gt_dump.py) NPZ OUT.json
Geometry is parsed from 3_place.def / LEF text with gt_raw's own parsers (no builder code).
Channel definitions follow the rasterize.py docstring (definition-from-code where it is silent:
pin point = OpenDB average of pin-shape centres, RUDY bbox floor 0.1 um, pin-RUDY at bbox corners).
"""
import json, math, re, sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gt_raw import parse_lef, parse_components, parse_pins, parse_nets, def_header, parse_grt_log, find1  # noqa
from match import TOL  # noqa

ORI = {'N': lambda x, y, w, h: (x, y), 'S': lambda x, y, w, h: (w - x, h - y), 'FN': lambda x, y, w, h: (w - x, y),
       'FS': lambda x, y, w, h: (x, h - y), 'W': lambda x, y, w, h: (h - y, x), 'E': lambda x, y, w, h: (y, w - x),
       'FW': lambda x, y, w, h: (y, x), 'FE': lambda x, y, w, h: (h - y, w - x)}


def gcellgrid(def_path):
    xs, ys = set(), set()
    with open(def_path, errors='replace') as f:
        for line in f:
            if line.startswith('GCELLGRID'):
                t = line.split()
                s, n, st = int(t[2]), int(t[4]), int(t[6])
                (xs if t[1] == 'X' else ys).update(s + k * st for k in range(n))
            elif line.startswith('COMPONENTS'):
                break
    return sorted(xs), sorted(ys)


def raster_add(grid, xe, ye, box, value):
    """Exact area-overlap accumulation of `value` per unit area of the box (my own implementation)."""
    x0, y0, x1, y1 = box
    i0 = max(int(np.searchsorted(xe, x0, 'right')) - 1, 0); i1 = min(int(np.searchsorted(xe, x1, 'left')), len(xe) - 1)
    j0 = max(int(np.searchsorted(ye, y0, 'right')) - 1, 0); j1 = min(int(np.searchsorted(ye, y1, 'left')), len(ye) - 1)
    if i1 <= i0 or j1 <= j0:
        return
    ox = np.clip(np.minimum(x1, xe[i0 + 1:i1 + 1]) - np.maximum(x0, xe[i0:i1]), 0, None)
    oy = np.clip(np.minimum(y1, ye[j0 + 1:j1 + 1]) - np.maximum(y0, ye[j0:j1]), 0, None)
    grid[j0:j1, i0:i1] += value * np.outer(oy, ox)


def bin_of(edges, v):
    return min(max(int(np.searchsorted(edges, v, 'right')) - 1, 0), len(edges) - 2)


def main(design_dir, tech_dir, truth_dir, npz_path, out):
    d, T = Path(design_dir), Path(tech_dir)
    from gt_raw import parse_liberty
    lib, _ = parse_liberty([T / 'lib/NangateOpenCellLibrary_typical.lib'])
    layers, macros = parse_lef([T / 'lef/NangateOpenCellLibrary.tech.lef', T / 'lef/NangateOpenCellLibrary.macro.mod.lef'])
    place = d / '3_place.def'
    dbu, die, _ = def_header(place)
    comps = parse_components(place); pins = parse_pins(place); nets, _, _ = parse_nets(place)
    gr = json.load(open(Path(truth_dir) / 'gr_truth.json'))
    z = np.load(npz_path, allow_pickle=False)
    res = {}
    # ---- grid
    dx, dy = gcellgrid(d / '5_route.def')
    res['grid_def_equals_odb'] = dict(passed=(dx == gr['grid_x_dbu'] and dy == gr['grid_y_dbu']), n_def=[len(dx), len(dy)], n_odb=[len(gr['grid_x_dbu']), len(gr['grid_y_dbu'])])
    xe = np.array([v / dbu for v in gr['grid_x_dbu']] + [gr['die'][2] / dbu]); ye = np.array([v / dbu for v in gr['grid_y_dbu']] + [gr['die'][3] / dbu])
    res['grid_edges'] = dict(passed=bool(z['x_edges_um'].shape == xe.shape and z['y_edges_um'].shape == ye.shape
                                         and np.allclose(z['x_edges_um'], xe, atol=1e-3) and np.allclose(z['y_edges_um'], ye, atol=1e-3)),
                             claim_shape=[int(z['x_edges_um'].size), int(z['y_edges_um'].size)], truth_shape=[xe.size, ye.size])
    nx, ny = len(xe) - 1, len(ye) - 1
    area = np.outer(np.diff(ye), np.diff(xe))
    # ---- label
    Hc = np.zeros((ny, nx)); Hu = np.zeros((ny, nx)); Vc = np.zeros((ny, nx)); Vu = np.zeros((ny, nx))
    grt = parse_grt_log(find1(d, 'logs/**/5_1_grt.log'))
    table = []
    for name, L in gr['layers'].items():
        cap, use = np.array(L['cap'], float), np.array(L['use'], float)
        if L['dir'] == 'HORIZONTAL':
            Hc += cap; Hu += use
        else:
            Vc += cap; Vu += use
        f = grt['final'].get(name)
        if f:
            table.append(dict(layer=name, cap_sum=int(cap.sum()), grt_resource=f['resource'], use_sum=int(use.sum()), grt_demand=f['demand']))
    with np.errstate(divide='ignore', invalid='ignore'):
        ch = np.where(Hc > 0, Hu / Hc, 0.0); cv = np.where(Vc > 0, Vu / Vc, 0.0)
    truthY = np.stack([ch, cv])
    res['label_cong'] = cmp(z['cong'], truthY, TOL['img_label'])
    res['label_vs_grt0096_table'] = dict(applicable=False, rows=table,
                                         resource_equal=all(r['cap_sum'] == r['grt_resource'] for r in table),
                                         demand_equal=all(r['use_sum'] == r['grt_demand'] for r in table))
    # ---- inputs (3_place.def, own geometry)
    power = {}
    for line in open(Path(truth_dir) / 'power_truth.tsv'):
        n, w = line.rstrip('\n').split('\t')
        try:
            power[n.replace('\\', '')] = float(w)
        except ValueError:
            power[n.replace('\\', '')] = 0.0
    cell = np.zeros((ny, nx)); macro = np.zeros((ny, nx)); pinc = np.zeros((ny, nx)); pw = np.zeros((ny, nx)); pw_noesc = np.zeros((ny, nx))
    for name, c in comps.items():
        m = macros[c['master']]
        if c['x'] is None:
            continue
        w, h = (m['h'], m['w']) if c['orient'] in ('W', 'E', 'FW', 'FE') else (m['w'], m['h'])
        x0, y0 = c['x'] / dbu, c['y'] / dbu
        box = (x0, y0, round(c['x'] + w * dbu) / dbu, round(c['y'] + h * dbu) / dbu)
        A = max((box[2] - box[0]) * (box[3] - box[1]), 1e-9)
        raster_add(macro if m['cls'] == 'BLOCK' else cell, xe, ye, box, 1.0)
        raster_add(pw, xe, ye, box, power.get(name, 0.0) / A)
        if '[' not in name and ']' not in name:  # diagnostic: escaped-name instances dropped
            raster_add(pw_noesc, xe, ye, box, power.get(name, 0.0) / A)
        # OpenDB sig type SIGNAL: LEF USE SIGNAL and not a Liberty clock pin (definition-from-code)
        lp = lib.get(c['master'], {}).get('pins', {})
        npins = sum(1 for pn_, p in m['pins'].items() if (p['use'] or 'SIGNAL') == 'SIGNAL' and not lp.get(pn_, {}).get('clock'))
        pinc[bin_of(ye, (box[1] + box[3]) / 2), bin_of(xe, (box[0] + box[2]) / 2)] += npins

    def avg_xy(inst, pin):
        c = comps.get(inst)
        m = macros[c['master']]; p = m['pins'].get(pin)
        if c['x'] is None or not p or not p['rects']:
            return None
        sx = sy = 0
        for r in p['rects']:
            ax, ay = ORI[c['orient']](r[0], r[1], m['w'], m['h']); bx, by = ORI[c['orient']](r[2], r[3], m['w'], m['h'])
            lx, hx = sorted((round(ax * dbu), round(bx * dbu))); ly, hy = sorted((round(ay * dbu), round(by * dbu)))
            sx += c['x'] + lx + (hx - lx) // 2; sy += c['y'] + ly + (hy - ly) // 2
        n = len(p['rects'])
        return sx // n, sy // n

    def bpin_centre(io):
        q = pins.get(io)
        if not q or q['x'] is None or q['shape'] is None:
            return None
        a, b, c2, e = q['shape']
        o = q['orient'] or 'N'
        rot = {'N': (a, b, c2, e), 'S': (-c2, -e, -a, -b), 'E': (b, -c2, e, -a), 'W': (-e, a, -b, c2),
               'FN': (-c2, b, -a, e), 'FS': (a, -e, c2, -b), 'FE': (b, a, e, c2), 'FW': (-e, -c2, -b, -a)}[o]
        return (q['x'] + rot[0] + q['x'] + rot[2]) // 2, (q['y'] + rot[1] + q['y'] + rot[3]) // 2

    rudy = np.zeros((ny, nx)); prudy = np.zeros((ny, nx))
    for nm, r in nets.items():
        if r['use'] != 'SIGNAL':
            continue
        pts = [p for p in (avg_xy(i, p) for i, p in r['conns']) if p]
        pts += [p for p in (bpin_centre(io) for io in r['ios']) if p]
        if len(pts) < 2:
            continue
        x0 = min(p[0] for p in pts) / dbu; x1 = max(p[0] for p in pts) / dbu
        y0 = min(p[1] for p in pts) / dbu; y1 = max(p[1] for p in pts) / dbu
        w, h = max(x1 - x0, 0.1), max(y1 - y0, 0.1)
        dens = (w + h) / (w * h)
        raster_add(rudy, xe, ye, (x0, y0, x0 + w, y0 + h), dens)
        for px, py in ((x0, y0), (x1, y1)):
            prudy[bin_of(ye, py), bin_of(xe, px)] += dens * len(pts) / 2.0
    X = np.stack([cell / area, pinc / area, rudy / area, prudy, macro / area, pw / area])
    names = list(z['channels'])
    for k, ch_name in enumerate(['cell_density', 'pin_density', 'rudy', 'pin_rudy', 'macro_mask', 'power_density']):
        res[f'X_{ch_name}'] = cmp(z['X'][k], X[k], None) if names[k] == ch_name else dict(passed=False, error='channel order ' + str(names))
    d_ = cmp(z['X'][5], pw_noesc / area, None); d_['applicable'] = False
    res['diag_power_if_escaped_names_dropped'] = d_
    tot_t = float((pw * 1).sum()); tot_c = float((z['X'][5] * area).sum())
    res['diag_power_total_W'] = dict(applicable=False, truth=float((pw).sum()), claim=tot_c, dropped_fraction=1 - tot_c / tot_t if tot_t else None)
    res['verdict'] = dict(passed=all(v.get('passed', True) for k, v in res.items() if isinstance(v, dict) and v.get('applicable', True)),
                          failed=[k for k, v in res.items() if isinstance(v, dict) and v.get('applicable', True) and not v.get('passed', True)])
    Path(out).write_text(json.dumps(res, indent=1, default=float))
    print('IMG', Path(design_dir).name, 'PASS' if res['verdict']['passed'] else 'FAIL', res['verdict']['failed'])


def cmp(a, b, tol):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if a.shape != b.shape:
        return dict(passed=False, error=f'shape {a.shape} vs {b.shape}')
    if tol is None:
        # Amendment A6 (2026-11-07): the frozen '+1e-6' absolute floor exceeded the whole power-density
        # channel (~1e-6 W/um^2) and hid a real defect; the floor is now relative to the channel maximum
        # (float32 storage precision ~1e-7 relative, so 1e-4*max is still 1000x looser than storage).
        atol = 1e-4 * float(np.abs(b).max()); rtol = 0.0
    else:
        atol, rtol = tol
    e = np.abs(a - b)
    ok = e <= atol + rtol * np.abs(b)
    return dict(n=int(b.size), matched=int(ok.sum()), rate=float(ok.mean()), passed=bool(ok.all()), max_abs=float(e.max()),
                tol_abs=atol, max_truth=float(np.abs(b).max()), worst_pixel=[int(v) for v in np.unravel_index(int(e.argmax()), e.shape)])


if __name__ == '__main__':
    main(*sys.argv[1:6])
