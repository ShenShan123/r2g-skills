#!/usr/bin/env python3
"""Check def-graph's image (GCell feature-map) dataset against independent truth.

Truth: grid + congestion fields from truth_defgraph.pkl (routed DEF GCELLGRID, own gaussian);
features re-rasterised here from the raw 3_place.def with gt_raw parsers (channel definitions =
build_images.py docstring / SKILL.md §5; pin point = mean of LEF RECT centres, oriented).
Usage: v1_images_dg.py DESIGN_DIR PLATFORM_DIR TRUTH.pkl IMAGES_DIR OUT.json [PLATFORM [PLACE_DEF]]
"""
import json, math, pickle, sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gt_raw import parse_lef, def_header, parse_components, parse_pins, parse_nets, orient_point, oriented_size  # noqa
from adapter_defgraph import load_images  # noqa


def cmp(a, b, rel=1e-4, absf=1e-9):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if a.shape != b.shape:
        return dict(passed=False, error=f'shape {a.shape} vs {b.shape}')
    tol = rel * max(float(np.abs(b).max()), 1e-30) + absf
    e = np.abs(a - b)
    return dict(passed=bool((e <= tol).all()), n=int(b.size), matched=int((e <= tol).sum()), max_abs=float(e.max()) if e.size else 0.0,
                tol=tol, worst=[int(v) for v in np.unravel_index(int(e.argmax()), e.shape)] if e.size else None)


def main():
    ddir, pdir, T, idir, outp = Path(sys.argv[1]), Path(sys.argv[2]), pickle.load(open(sys.argv[3], 'rb')), Path(sys.argv[4]), Path(sys.argv[5])
    I = load_images(idir); f, lb = I['features'], I['labels']; R = {}
    g = T['grid']; nx, ny, sx, sy = g['nx'], g['ny'], g['sx'], g['sy']
    R['grid_shape'] = dict(passed=all(v.shape == (nx, ny) for v in list(f.values()) + list(lb.values()) if hasattr(v, 'shape') and v.ndim == 2),
                           truth=[nx, ny], claim={k: list(v.shape) for k, v in {**f, **lb}.items() if hasattr(v, 'shape')})
    R['label_congestion'] = cmp(lb['congestion'], g['G'], 1e-6)
    R['label_congestion_raw'] = cmp(lb['congestion_raw'], g['U'], 1e-6)
    # features from the placement DEF
    # v2 (2026-11-07): optional argv[6] platform (platforms_v2), argv[7] placement DEF path
    plat = sys.argv[6] if len(sys.argv) > 6 else 'nangate45'
    from platforms_v2 import files as plat_files
    layers, macros = parse_lef(plat_files(plat, pdir)['lefs'])
    place = Path(sys.argv[7]) if len(sys.argv) > 7 else ddir / '3_place.def'
    dbu, die, _ = def_header(place); comps = parse_components(place); pins = parse_pins(place); nets, _, _ = parse_nets(place)
    x0, y0, x1, y1 = (v / dbu for v in die); gx, gy = sx / dbu, sy / dbu
    xe = np.minimum(x0 + np.arange(nx + 1) * gx, x1); ye = np.minimum(y0 + np.arange(ny + 1) * gy, y1)
    area = np.outer(np.diff(xe), np.diff(ye))

    def splat(arr, r, v):
        xa, ya, xb, yb = max(r[0], x0), max(r[1], y0), min(r[2], x1), min(r[3], y1)
        if xb <= xa or yb <= ya:
            return
        ox = np.clip(np.minimum(xb, xe[1:]) - np.maximum(xa, xe[:-1]), 0, None)
        oy = np.clip(np.minimum(yb, ye[1:]) - np.maximum(ya, ye[:-1]), 0, None)
        arr += v * np.outer(ox, oy)

    def cell(x, y):
        # Amendment A9 (2026-11-07): bin in exact DBU. In float um a point ON a GCell line
        # (28500 dbu, 5700-dbu step: 14.25 // 2.85 == 4.0) fell one GCell low -- the same rounding
        # def-graph's build_images.Grid had, so this check shared the builder's error.
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return None
        bx = int(round(x * dbu - die[0], 6) // sx); by = int(round(y * dbu - die[1], 6) // sy)
        return min(bx, nx - 1), min(by, ny - 1)

    std = np.zeros((nx, ny)); mac = np.zeros((nx, ny))
    for name, c in comps.items():
        m = macros.get(c['master'])
        if c['x'] is None or not m:
            continue
        w, h = oriented_size(m['w'], m['h'], c['orient'])
        r = (c['x'] / dbu, c['y'] / dbu, c['x'] / dbu + w, c['y'] / dbu + h)
        splat(mac if m['cls'] == 'BLOCK' else std, r, 1.0)
    with np.errstate(divide='ignore', invalid='ignore'):
        R['X_cell_density'] = cmp(f['cell_density'], np.where(area > 0, std / area, 0.0))
        R['X_macro_region'] = cmp(f['macro_region'], np.where(area > 0, mac / area, 0.0))

    def pin_pt(i, p):
        if i == 'PIN':
            q = pins.get(p); return (q['x'] / dbu, q['y'] / dbu) if q and q['x'] is not None else None
        c = comps.get(i); m = macros.get(c['master']) if c else None
        if not c or c['x'] is None:
            return None
        if not m or p not in m['pins'] or not m['pins'][p]['rects']:
            return (c['x'] / dbu, c['y'] / dbu)
        rr = m['pins'][p]['rects']
        cx = sum((a[0] + a[2]) / 2 for a in rr) / len(rr); cy = sum((a[1] + a[3]) / 2 for a in rr) / len(rr)
        ox, oy = orient_point(cx, cy, m['w'], m['h'], c['orient'])
        return (c['x'] / dbu + ox, c['y'] / dbu + oy)

    rudy = np.zeros((nx, ny)); prudy = np.zeros((nx, ny)); pden = np.zeros((nx, ny)); pden_all = np.zeros((nx, ny))
    for n, r in nets.items():
        if r['use'] in ('POWER', 'GROUND'):
            continue
        pts = [q for q in (pin_pt(i, p) for i, p in r['conns']) if q] + [q for q in (pin_pt('PIN', io) for io in r['ios']) if q]
        for q in pts:
            ij = cell(*q)
            if ij:
                pden_all[ij] += 1
        if len(pts) < 2:
            continue
        xa, xb = min(q[0] for q in pts), max(q[0] for q in pts); ya, yb = min(q[1] for q in pts), max(q[1] for q in pts)
        w, h = max(xb - xa, gx), max(yb - ya, gy); cx, cy = (xa + xb) / 2, (ya + yb) / 2
        d = (w + h) / (w * h)
        splat(rudy, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), d)
        for q in pts:
            ij = cell(*q)
            if ij:
                prudy[ij] += d; pden[ij] += 1
    with np.errstate(divide='ignore', invalid='ignore'):
        R['X_rudy'] = cmp(f['rudy'], np.where(area > 0, rudy / area, 0.0))
    R['X_pin_rudy'] = cmp(f['pin_rudy'], prudy)
    R['X_pin_density'] = cmp(f['pin_density'], pden, 0.0, 1e-9)
    d_ = cmp(f['pin_density'], pden_all, 0.0, 1e-9); d_['applicable'] = False
    R['diag_pin_density_all_signal_pins_incl_single_pin_nets'] = d_
    R['verdict'] = dict(passed=all(v.get('passed', True) for k, v in R.items() if isinstance(v, dict) and v.get('applicable', True)),
                        failed=[k for k, v in R.items() if isinstance(v, dict) and v.get('applicable', True) and not v.get('passed', True)])
    outp.write_text(json.dumps(R, indent=1, default=float))
    print('DGIMG', ddir.name[:40], 'PASS' if R['verdict']['passed'] else 'FAIL', R['verdict']['failed'])


if __name__ == '__main__':
    main()
