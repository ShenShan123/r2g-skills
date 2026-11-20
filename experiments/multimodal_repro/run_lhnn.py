#!/usr/bin/env python3
"""LHNN (Wang et al., DAC 2022, "LHNN: Lattice Hypergraph Neural Network for VLSI Congestion Prediction",
arXiv:2203.12831) -- re-implemented from the paper; no official code is released.

  run_lhnn.py --data DIR --seed S --out OUT [--epochs 300] [--train-frac 1.0]

LH-graph (Sec. 3): G-cells = the label grid; a G-net = the set of G-cells inside a net's placement bounding box;
G-cell <-> G-cell lattice edges (4-neighbour); G-cell <-> G-net hyperedges (incidence H).
  G-cell features (4): horizontal / vertical net density (sum over covering G-nets of 1/span_V, 1/span_H),
                       pin density, terminal mask (G-cell holds an IO terminal or macro)
  G-net features (4):  span_V, span_H, #pins, area (= span_H x span_V)
Model (Sec. 4, Fig. 3; hidden 32): FeatureGen  Vc1 = phi_c(f_c(Vc0) || G_nc f_n(Vn0)),  Vn1 = phi_n(f_n(Vn0));
2 HyperMP blocks (G-cell->G-net then G-net->G-cell, residual MLPs, fusion with Vn1 / Vc1 by concat + linear,
mean aggregation G_cn = B^-1 H^T, G_nc = D^-1 H); 1 LatticeMP block (A' = P^-1 A, skip connection);
then a regression and a classification branch of 2 LatticeMP blocks each.
Loss (Eq. 3-5): MSE(regression) + BCE(classification) with weight gamma = 0.7 on non-congested G-cells.
Optimiser: Adam, lr 2e-3 (Sec. 5.1).
Adaptations, and why:
  * class label: the paper's "demand exceeds capacity" never occurs in signed-off designs (max utilisation 0.59),
    so the classification target is the design's top-10% G-cells (the hotspot definition our F1 metric uses);
  * the paper drops G-nets covering > 0.25% of all G-cells, tuned to superblue-size grids; on our 32-100-wide grids
    that would drop nearly every net, so we drop G-nets covering > 10% of the G-cells;
  * full-neighbourhood message passing instead of DGL neighbour sampling {6,3,2} (our graphs fit in memory);
  * epochs (unstated): 300, checkpoint by validation loss.
"""
import argparse
import os
import random
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402

MAX_GNET_FRAC = 0.10


def build_lh(s):
    g = s["g"]
    M, N = s["grid"]
    nfoot = g["net_foot"].astype(np.int64)                         # x0, y0, x1, y1 inclusive
    deg = np.bincount(g["pin_net"], minlength=len(nfoot)).astype(np.float32)
    sh, sv = nfoot[:, 2] - nfoot[:, 0] + 1, nfoot[:, 3] - nfoot[:, 1] + 1
    area = sh * sv
    keep = np.flatnonzero(area <= MAX_GNET_FRAC * M * N)
    cs, ns = [], []
    for j, k in enumerate(keep):
        x0, y0, x1, y1 = nfoot[k]
        xx, yy = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1), indexing="ij")
        cs.append((xx * N + yy).ravel()); ns.append(np.full(xx.size, j))
    cs, ns = np.concatenate(cs), np.concatenate(ns)
    hden = np.bincount(cs, weights=1.0 / sv[keep][ns], minlength=M * N)
    vden = np.bincount(cs, weights=1.0 / sh[keep][ns], minlength=M * N)
    pxy = g["pin_xy"]
    pg = np.clip((pxy[:, 0] * M).astype(int), 0, M - 1) * N + np.clip((pxy[:, 1] * N).astype(int), 0, N - 1)
    pden = np.bincount(pg, minlength=M * N)
    term = np.zeros(M * N)
    cf, kind, cfoot = g["cell_feat"], g["cell_kind"], g["cell_foot"]
    for i in np.flatnonzero(kind == 1):
        term[cfoot[i, 0] * N + cfoot[i, 1]] = 1
    term = np.maximum(term, (s["feat"]["macro_region"] > 0).ravel())
    vc = np.stack([hden, vden, pden, term], 1).astype(np.float32)
    vn = np.stack([sv[keep], sh[keep], deg[keep], area[keep]], 1).astype(np.float32)
    ii = np.arange(M * N).reshape(M, N)
    e = [np.stack([ii[:-1].ravel(), ii[1:].ravel()]), np.stack([ii[:, :-1].ravel(), ii[:, 1:].ravel()])]
    lat = np.concatenate(e + [x[::-1] for x in e], 1)
    y = s["y"].ravel().astype(np.float32)
    k = max(1, int(round(0.1 * y.size)))
    ycls = (y >= np.sort(y)[-k]).astype(np.float32)
    return {"vc": vc, "vn": vn, "inc_c": cs, "inc_n": ns, "lat": lat, "y": y, "ycls": ycls, "M": M, "N": N}


def mean_agg(src, idx_src, idx_dst, n_dst):
    out = src.new_zeros(n_dst, src.shape[1]).index_add_(0, idx_dst, src[idx_src])
    cnt = src.new_zeros(n_dst).index_add_(0, idx_dst, torch.ones_like(idx_dst, dtype=src.dtype)).clamp(min=1)
    return out / cnt[:, None]


class Res(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.f = nn.Sequential(nn.Linear(d, d), nn.ReLU(), nn.Linear(d, d))

    def forward(self, x):
        return F.relu(x + self.f(x))


class HyperMP(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.rc, self.rn, self.rc2, self.rn2 = Res(d), Res(d), Res(d), Res(d)
        self.fn, self.fc = nn.Linear(2 * d, d), nn.Linear(2 * d, d)

    def forward(self, vc, vn, vc1, vn1, b):
        agg_n = mean_agg(self.rc(vc), b["inc_c"], b["inc_n"], vn.shape[0])                 # G_cn = B^-1 H^T
        vn = self.rn(vn) + F.relu(self.fn(torch.cat([agg_n, vn1], 1)))
        agg_c = mean_agg(self.rn2(vn), b["inc_n"], b["inc_c"], vc.shape[0])                # G_nc = D^-1 H
        vc = self.rc2(vc) + F.relu(self.fc(torch.cat([agg_c, vc1], 1)))
        return vc, vn


class LatticeMP(nn.Module):
    def __init__(self, d):
        super().__init__()
        self.lin = nn.Linear(d, d)

    def forward(self, vc, b):
        return vc + F.relu(self.lin(mean_agg(vc, b["lat"][0], b["lat"][1], vc.shape[0])))   # P^-1 A, skip


class LHNN(nn.Module):
    def __init__(self, d=32):
        super().__init__()
        self.fc0, self.fn0 = nn.Linear(4, d), nn.Linear(4, d)
        self.phi_c, self.phi_n = nn.Linear(2 * d, d), nn.Linear(d, d)
        self.hyper = nn.ModuleList([HyperMP(d), HyperMP(d)])
        self.lat = LatticeMP(d)
        self.reg = nn.ModuleList([LatticeMP(d), LatticeMP(d)])
        self.cls = nn.ModuleList([LatticeMP(d), LatticeMP(d)])
        self.out_r, self.out_c = nn.Linear(d, 1), nn.Linear(d, 1)

    def forward(self, b):
        hc, hn = self.fc0(b["vc"]), self.fn0(b["vn"])
        vc1 = F.relu(self.phi_c(torch.cat([hc, mean_agg(hn, b["inc_n"], b["inc_c"], hc.shape[0])], 1)))
        vn1 = F.relu(self.phi_n(hn))
        vc, vn = vc1, vn1
        for blk in self.hyper:
            vc, vn = blk(vc, vn, vc1, vn1, b)
        vc = self.lat(vc, b)
        r, c = vc, vc
        for blk in self.reg:
            r = blk(r, b)
        for blk in self.cls:
            c = blk(c, b)
        return self.out_r(r)[:, 0], torch.sigmoid(self.out_c(c)[:, 0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--gamma", type=float, default=0.7)
    ap.add_argument("--train-frac", type=float, default=1.0)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    torch.manual_seed(a.seed); np.random.seed(a.seed); random.seed(a.seed)
    names = bench.designs(a.data)
    cells = bench.cells_of(a.data, names)
    tr, va, te = bench.split(names, cells)
    tr = bench.subsample(tr, cells, a.train_frac)
    t0 = time.time()
    B = {n: build_lh(bench.load(a.data, n)) for n in tr + va + te}
    # log1p + train-split z-score of the input features (the paper does not state its scaling)
    vc_all = np.concatenate([np.log1p(B[n]["vc"]) for n in tr]); vn_all = np.concatenate([np.log1p(B[n]["vn"]) for n in tr])
    mc, sc = vc_all.mean(0), vc_all.std(0) + 1e-6
    mn, sn = vn_all.mean(0), vn_all.std(0) + 1e-6
    dev = torch.device(a.device)
    T = {}
    for n, b in B.items():
        T[n] = {"vc": torch.tensor((np.log1p(b["vc"]) - mc) / sc, device=dev),
                "vn": torch.tensor((np.log1p(b["vn"]) - mn) / sn, device=dev),
                "inc_c": torch.tensor(b["inc_c"], device=dev), "inc_n": torch.tensor(b["inc_n"], device=dev),
                "lat": torch.tensor(b["lat"], device=dev), "y": torch.tensor(b["y"], device=dev),
                "ycls": torch.tensor(b["ycls"], device=dev)}
    model = LHNN().to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr)

    def loss_fn(r, c, b):
        w = (1 - b["ycls"]) * a.gamma + b["ycls"]
        bce = -(w * (b["ycls"] * torch.log(c.clamp(min=1e-7)) + (1 - b["ycls"]) * torch.log((1 - c).clamp(min=1e-7)))).mean()
        return F.mse_loss(r, b["y"]) + bce

    best, best_state = float("inf"), None
    for ep in range(a.epochs):
        model.train()
        for n in random.sample(tr, len(tr)):
            r, c = model(T[n])
            loss = loss_fn(r, c, T[n])
            opt.zero_grad(); loss.backward(); opt.step()
        if ep % 5 == 4 or ep == a.epochs - 1:
            model.eval()
            with torch.no_grad():
                vl = float(np.mean([loss_fn(*model(T[n]), T[n]).item() for n in va]))
            if vl < best:
                best, best_state = vl, {k: v.detach().clone() for k, v in model.state_dict().items()}
            if ep % 50 == 49:
                print(f"epoch {ep + 1} val loss {vl:.5f} best {best:.5f} {time.time() - t0:.0f}s", flush=True)
    model.load_state_dict(best_state)
    model.eval()
    per = {}
    with torch.no_grad():
        for n in te:
            r, _ = model(T[n])
            M, N = B[n]["M"], B[n]["N"]
            per[n] = bench.score(r.clamp(min=0).cpu().numpy().reshape(M, N), B[n]["y"].reshape(M, N))
    bench.write_result(a.out, "lhnn", a.seed, per, {"n_train": len(tr), "n_test": len(te), "best_val_loss": best,
                                                     "params": sum(p.numel() for p in model.parameters()),
                                                     "wall_s": round(time.time() - t0, 1), "train_frac": a.train_frac})


if __name__ == "__main__":
    main()
