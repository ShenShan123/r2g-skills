#!/usr/bin/env python3
"""Per-net rectilinear spanning trees on the gcell grid (topology-aware routing demand), from graph.npz.

  mst_prep.py DATA_DIR [--workers 16]

For every signal net: pins -> gcell coordinates (die-normalised pin_xy x die size / gcell step); a rectilinear
minimum spanning tree (Prim on Manhattan distance) for nets with <= 64 pins, a star from the median pin above that.
Each tree edge (p,q) is routed as the AVERAGE of its two L-shapes: weight 0.5 on (h at y_p from x_p to x_q, v at x_q)
and 0.5 on (v at x_p, h at y_q). Segments are stored as gcell rectangles with a direction and weight, so a model can
splat per-net quantities along the tree instead of uniformly over the bounding box (RUDY's approximation).
Writes DATA_DIR/<design>/mst.npz: seg_net[S], seg_rect[S,4] (x0,y0,x1,y1 inclusive), seg_dir[S] (0=h,1=v),
seg_w[S], and the fixed maps mst_h/mst_v [nx,ny] = segment coverage per gcell (wire length in gcells).
"""
import argparse
import json
import os
from multiprocessing import Pool

import numpy as np


def rmst(pts):
    k = len(pts)
    if k <= 1:
        return []
    if k > 64:
        c = np.median(pts, 0)
        hub = int(np.argmin(np.abs(pts - c).sum(1)))
        return [(hub, j) for j in range(k) if j != hub]
    inn = np.zeros(k, bool); inn[0] = True
    d = np.abs(pts - pts[0]).sum(1); par = np.zeros(k, int)
    edges = []
    for _ in range(k - 1):
        dd = np.where(inn, np.inf, d)
        j = int(np.argmin(dd)); edges.append((par[j], j)); inn[j] = True
        nd = np.abs(pts - pts[j]).sum(1)
        upd = nd < d; d = np.where(upd, nd, d); par = np.where(upd, j, par)
    return edges


def one(dd):
    try:
        G = np.load(os.path.join(dd, "graph.npz"))
        g = json.load(open(os.path.join(dd, "labels", "congestion_grid.json")))
        nx, ny = int(G["grid"][0]), int(G["grid"][1])
        dbu = float(g["db_units"]); die = [v / dbu for v in g["die_dbu"]]
        sx, sy = g["grid_step_dbu"][0] / dbu, g["grid_step_dbu"][1] / dbu
        gxy = np.stack([G["pin_xy"][:, 0] * (die[2] - die[0]) / sx, G["pin_xy"][:, 1] * (die[3] - die[1]) / sy], 1)
        gi = np.stack([np.clip(np.floor(gxy[:, 0]), 0, nx - 1), np.clip(np.floor(gxy[:, 1]), 0, ny - 1)], 1).astype(np.int32)
        pn = G["pin_net"]
        order = np.argsort(pn, kind="stable"); pn_s = pn[order]
        bounds = np.flatnonzero(np.diff(np.concatenate([[-1], pn_s, [pn_s[-1] + 1 if len(pn_s) else 0]])))
        sn, sr, sd, sw = [], [], [], []
        for a, b in zip(bounds[:-1], bounds[1:]):
            idx = order[a:b]; net = int(pn[idx[0]])
            pts = gi[idx]
            pts = np.unique(pts, axis=0)
            for i, j in rmst(pts):
                (x0, y0), (x1, y1) = pts[i], pts[j]
                for (hy, vx, w) in ((y0, x1, 0.5), (y1, x0, 0.5)):
                    if x0 != x1:
                        sn.append(net); sr.append((min(x0, x1), hy, max(x0, x1), hy)); sd.append(0); sw.append(w)
                    if y0 != y1:
                        sn.append(net); sr.append((vx, min(y0, y1), vx, max(y0, y1))); sd.append(1); sw.append(w)
        sr = np.asarray(sr, np.int32).reshape(-1, 4); sd = np.asarray(sd, np.int8); sw = np.asarray(sw, np.float32)
        mh = np.zeros((nx + 1, ny + 1), np.float64); mv = np.zeros((nx + 1, ny + 1), np.float64)
        for m, dsel in ((mh, 0), (mv, 1)):
            r, w = sr[sd == dsel], sw[sd == dsel]
            np.add.at(m, (r[:, 0], r[:, 1]), w); np.add.at(m, (r[:, 2] + 1, r[:, 1]), -w)
            np.add.at(m, (r[:, 0], r[:, 3] + 1), -w); np.add.at(m, (r[:, 2] + 1, r[:, 3] + 1), w)
        mh = mh.cumsum(0).cumsum(1)[:nx, :ny]; mv = mv.cumsum(0).cumsum(1)[:nx, :ny]
        np.savez_compressed(os.path.join(dd, "mst.npz"), seg_net=np.asarray(sn, np.int32), seg_rect=sr, seg_dir=sd,
                            seg_w=sw, mst_h=mh.astype(np.float32), mst_v=mv.astype(np.float32))
        return os.path.basename(dd), len(sn), None
    except Exception as e:                                       # noqa: BLE001
        return os.path.basename(dd), 0, repr(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    ds = sorted(os.path.join(a.data, n) for n in os.listdir(a.data) if os.path.isfile(os.path.join(a.data, n, "graph.npz")))
    with Pool(a.workers) as p:
        res = p.map(one, ds)
    bad = [r for r in res if r[2]]
    print(f"{len(res)} designs, {sum(r[1] for r in res)} segments, {len(bad)} errors", bad[:3])


if __name__ == "__main__":
    main()
