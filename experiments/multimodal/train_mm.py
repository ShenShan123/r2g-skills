#!/usr/bin/env python3
"""Train/evaluate one model on the multimodal congestion dataset (design-disjoint split).

  train_mm.py --data DIR --model unet|gnn|graft|exchange --seed S --out OUT [--epochs 150] [--device cuda:0]

DIR holds one sub-directory per design with images/{features,labels}.npz and graph.npz (mm_prep.py).
Split: grouped by source repository (3rd field of the design name) so no repository is in two splits, stratified
by cell count, fixed by --split-seed (shared by every model). Target: routed demand/capacity per gcell
(`congestion_raw`, unsmoothed). Inputs: the 5 placement channels (log1p, z-scored on train) and/or the netlist.
Protocol (as the paper's CNN study): AdamW lr 1e-3, cosine, MSE, D8 augmentation, checkpoint by validation NRMSE.
Writes OUT/metrics.json (per-design test metrics) and OUT/train_log.json.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import time

import numpy as np
import torch
import torch.nn.functional as F

from mm_models import build

FEATS = ["cell_density", "macro_region", "rudy", "pin_rudy", "pin_density"]


def load_design(d):
    F_ = np.load(os.path.join(d, "images", "features.npz"))
    L = np.load(os.path.join(d, "images", "labels.npz"))
    G = np.load(os.path.join(d, "graph.npz"))
    x = np.stack([np.log1p(F_[k].astype(np.float32)) for k in FEATS])
    y = L["congestion_raw"].astype(np.float32)[None]
    cf = G["cell_feat"]
    cell_num = np.stack([np.log1p(cf[:, 1]), np.log1p(cf[:, 2]), cf[:, 3], cf[:, 4], np.log1p(cf[:, 5]),
                         G["cell_kind"].astype(np.float32)], 1).astype(np.float32)
    nf = G["net_feat"]
    net_num = np.log1p(nf).astype(np.float32)
    o = {"x": x, "y": y, "master": cf[:, 0].astype(np.int64), "cell_num": cell_num, "net_num": net_num,
            "pin_cell": G["pin_cell"].astype(np.int64), "pin_net": G["pin_net"].astype(np.int64),
            "pin_xy": G["pin_xy"].astype(np.float32), "cell_foot": G["cell_foot"].astype(np.int64),
            "net_foot": G["net_foot"].astype(np.int64), "n_masters": int(G["n_masters"][0])}
    mp = os.path.join(d, "mst.npz")
    if os.path.isfile(mp):
        M = np.load(mp)
        o.update(seg_net=M["seg_net"].astype(np.int64), seg_rect=M["seg_rect"].astype(np.int64),
                 seg_dir=M["seg_dir"].astype(np.int64), seg_w=M["seg_w"].astype(np.float32),
                 mst_x=np.stack([np.log1p(M["mst_h"]), np.log1p(M["mst_v"])]).astype(np.float32))
    return o


def split(names, cells, seed):
    """repository-grouped, size-stratified 70/15/15."""
    repo = {}
    for n in names:
        repo.setdefault(n.split("__")[2], []).append(n)
    groups = sorted(repo.values(), key=lambda g: (-max(cells[n] for n in g), g[0]))
    rng = random.Random(seed)
    tr, va, te = [], [], []
    # deal groups round-robin over size-sorted order in blocks of 20 (14/3/3) so every split spans all sizes
    for i in range(0, len(groups), 20):
        blk = groups[i:i + 20]
        rng.shuffle(blk)
        for j, gp in enumerate(blk):
            (tr if j < 14 else va if j < 17 else te).extend(gp)
    return tr, va, te


def augment(s, k):
    """D8 transform k in 0..7 applied consistently to image, label and graph geometry."""
    x, y = s["x"], s["y"]
    nx, ny = x.shape[1], x.shape[2]
    cf, nf_ = s["cell_foot"].copy(), s["net_foot"].copy()
    cn, pxy = s["cell_num"].copy(), s["pin_xy"].copy()
    fx, fy, tr = k & 1, (k >> 1) & 1, (k >> 2) & 1
    sr = s["seg_rect"].copy() if "seg_rect" in s else None
    sdir = s["seg_dir"].copy() if "seg_dir" in s else None
    mx = s["mst_x"] if "mst_x" in s else None
    if fx:
        if sr is not None:
            sr[:, [0, 2]] = nx - 1 - sr[:, [2, 0]]; mx = mx[:, ::-1]
        x, y = x[:, ::-1], y[:, ::-1]
        for r in (cf, nf_):
            r[:, [0, 2]] = nx - 1 - r[:, [2, 0]]
        cn[:, 2] = 1 - cn[:, 2]; pxy[:, 0] = 1 - pxy[:, 0]
    if fy:
        if sr is not None:
            sr[:, [1, 3]] = ny - 1 - sr[:, [3, 1]]; mx = mx[:, :, ::-1]
        x, y = x[:, :, ::-1], y[:, :, ::-1]
        for r in (cf, nf_):
            r[:, [1, 3]] = ny - 1 - r[:, [3, 1]]
        cn[:, 3] = 1 - cn[:, 3]; pxy[:, 1] = 1 - pxy[:, 1]
    if tr:
        if sr is not None:
            sr[:] = sr[:, [1, 0, 3, 2]]; sdir = 1 - sdir; mx = mx[::-1].transpose(0, 2, 1)   # h<->v swap
        x, y = x.transpose(0, 2, 1), y.transpose(0, 2, 1)
        for r in (cf, nf_):
            r[:] = r[:, [1, 0, 3, 2]]
        cn[:, [0, 1, 2, 3]] = cn[:, [1, 0, 3, 2]]; pxy[:] = pxy[:, [1, 0]]
    o = dict(s)
    o.update(x=np.ascontiguousarray(x), y=np.ascontiguousarray(y), cell_foot=cf, net_foot=nf_, cell_num=cn, pin_xy=pxy)
    if sr is not None:
        o.update(seg_rect=sr, seg_dir=sdir, mst_x=np.ascontiguousarray(mx))
    return o


def to_dev(s, dev, mu, sd):
    t = {k: torch.as_tensor(v).to(dev) for k, v in s.items() if isinstance(v, np.ndarray)}
    t["x"] = ((t["x"] - mu[:, None, None]) / sd[:, None, None])[None]
    if "mst_x" in t:
        t["mst_x"] = (t["mst_x"] / 3.0)[None]
    return t


def ssim(a, b):
    """global-window-free SSIM with an 7x7 uniform window, both maps scaled by the label range."""
    rng = max(float(b.max() - b.min()), 1e-6)
    a, b = torch.as_tensor(a / rng)[None, None], torch.as_tensor(b / rng)[None, None]
    k = 7 if min(a.shape[-2:]) >= 7 else min(a.shape[-2:])
    mu_a, mu_b = F.avg_pool2d(a, k, 1), F.avg_pool2d(b, k, 1)
    saa = F.avg_pool2d(a * a, k, 1) - mu_a ** 2
    sbb = F.avg_pool2d(b * b, k, 1) - mu_b ** 2
    sab = F.avg_pool2d(a * b, k, 1) - mu_a * mu_b
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2))
    return float(m.mean())


def auc(score, pos):
    order = np.argsort(score)
    ranks = np.empty(len(score)); ranks[order] = np.arange(1, len(score) + 1)
    npos = pos.sum(); nneg = len(pos) - npos
    if npos == 0 or nneg == 0:
        return float("nan")
    return float((ranks[pos].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def design_metrics(p, y):
    p, y = p.ravel().astype(np.float64), y.ravel().astype(np.float64)
    ss = ((y - y.mean()) ** 2).sum()
    r2 = 1 - ((y - p) ** 2).sum() / ss if ss > 0 else float("nan")
    nrmse = math.sqrt(((y - p) ** 2).mean()) / max(y.max() - y.min(), 1e-9)
    k = max(1, int(round(0.1 * len(y))))
    thr = np.sort(y)[-k]
    pos = y >= thr
    ptop = np.zeros_like(pos); ptop[np.argsort(p)[-pos.sum():]] = True
    tp = (pos & ptop).sum()
    f1 = 2 * tp / max(pos.sum() + ptop.sum(), 1)
    return {"r2": r2, "nrmse": nrmse, "f1_top10": float(f1), "auc_top10": auc(p, pos)}


def evaluate(model, data, names, dev, mu, sd):
    model.eval()
    out, P, Y = {}, [], []
    with torch.no_grad():
        for n in names:
            t = to_dev(data[n], dev, mu, sd)
            p = model(t["x"], t).clamp(min=0)[0, 0].float().cpu().numpy()
            y = data[n]["y"][0]
            m = design_metrics(p, y)
            m["ssim"] = ssim(p, y)
            out[n] = m
            P.append(p.ravel()); Y.append(y.ravel())
    P, Y = np.concatenate(P), np.concatenate(Y)
    pooled = 1 - ((Y - P) ** 2).sum() / ((Y - Y.mean()) ** 2).sum()
    return out, float(pooled)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--split-seed", type=int, default=20261118)
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True)
    ap.add_argument("--train-frac", type=float, default=1.0, help="use a stratified fraction of the train split")
    a = ap.parse_args()
    torch.manual_seed(a.seed); np.random.seed(a.seed); random.seed(a.seed)
    os.makedirs(a.out, exist_ok=True)
    names = sorted(n for n in os.listdir(a.data) if os.path.isfile(os.path.join(a.data, n, "graph.npz")))
    cells = {n: json.load(open(os.path.join(a.data, n, "sample.json")))["cells"] for n in names}
    tr, va, te = split(names, cells, a.split_seed)
    if a.train_frac < 1.0:
        tr = sorted(tr, key=lambda n: cells[n])[::max(1, int(round(1 / a.train_frac)))]
    t0 = time.time()
    data = {n: load_design(os.path.join(a.data, n)) for n in tr + va + te}
    xs = np.concatenate([data[n]["x"].reshape(5, -1) for n in tr], 1)
    mu = torch.tensor(xs.mean(1), dtype=torch.float32, device=a.device)
    sd = torch.tensor(xs.std(1) + 1e-6, dtype=torch.float32, device=a.device)
    n_masters = max(d["n_masters"] for d in data.values())
    model = build(a.model, n_masters).to(a.device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=1e-4)
    steps = a.epochs * math.ceil(len(tr) / a.accum)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.05)
    best, log = (float("inf"), None), []
    for ep in range(a.epochs):
        model.train()
        order = tr[:]; random.shuffle(order)
        tot = 0.0
        for i, n in enumerate(order):
            s = augment(data[n], random.randrange(8))
            t = to_dev(s, a.device, mu, sd)
            loss = F.mse_loss(model(t["x"], t), t["y"][None])
            (loss / a.accum).backward()
            tot += loss.item()
            if (i + 1) % a.accum == 0 or i + 1 == len(order):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step(); opt.zero_grad(); sched.step()
        if ep % 5 == 4 or ep == a.epochs - 1:
            vm, _ = evaluate(model, data, va, a.device, mu, sd)
            vn = float(np.mean([m["nrmse"] for m in vm.values()]))
            log.append({"epoch": ep + 1, "train_mse": tot / len(order), "val_nrmse": vn})
            if vn < best[0]:
                best = (vn, {k: v.detach().clone() for k, v in model.state_dict().items()})
    model.load_state_dict(best[1])
    tm, pooled = evaluate(model, data, te, a.device, mu, sd)
    params = sum(p.numel() for p in model.parameters())
    json.dump({"model": a.model, "seed": a.seed, "split_seed": a.split_seed, "n_train": len(tr), "n_val": len(va),
               "n_test": len(te), "params": params, "pooled_r2": pooled, "best_val_nrmse": best[0],
               "wall_s": round(time.time() - t0, 1), "per_design": tm, "test": te}, open(os.path.join(a.out, "metrics.json"), "w"), indent=1)
    json.dump(log, open(os.path.join(a.out, "train_log.json"), "w"), indent=1)
    r2 = np.nanmean([m["r2"] for m in tm.values()])
    print(f"{a.model} seed {a.seed}: test R2 {r2:.3f} pooled {pooled:.3f} F1 {np.mean([m['f1_top10'] for m in tm.values()]):.3f} params {params}")


if __name__ == "__main__":
    main()
