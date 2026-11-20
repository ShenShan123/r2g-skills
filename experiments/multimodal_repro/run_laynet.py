#!/usr/bin/env python3
"""Lay-Net (Zou et al., ICCAD 2023 / TCAD 2025, "Lay-Net: Grafting Netlist Knowledge on Layout-Based Congestion
Prediction") -- official code (lanchengzou/congPred @5b8bf5645171: LayNet.py, dataViG.py), trained with the
released train_laynet.py recipe on R2G.  The upstream repository carries no licence, so its files are NOT vendored
here: pass the reviewed checkout with --src (sha256 of the two files is recorded in metrics.json).

  run_laynet.py --src DIR --data DIR --cache DIR --seed S --out OUT [--no-netlist] [--train-frac 1.0]

Runs in the DGL venv (torch 2.4 + dgl 2.4, cu124): /home/yangao/r2g_toolchain/pyenvs/laynet.

Unchanged from the release: the model (LayNet(chIn=5, chOut=2, chMid=16, img_size=256, patch_size=4, embed_dim=32,
window 7, mlp_ratio 2, drop_path 0.2, depths [2,2,4,2], heads [2,4,8,16]) as in train_vig()), the input pipeline
(dataViG.loadFeat -> gen_vig_data / downsample / cal_macro_margin; dataViG.ViGSet builds the multi-scale
G-cell/G-net heterograph), the loss (MSE + 1e-6 x pixel contrastive), Adam(1e-3), StepLR(75, 0.5), 300 epochs,
batch 64, last epoch.
What we supply, since the release ships no feature extraction: loadFeat's input dict per design -- 256x256 maps
(density = cell_density, rudy, macro = macro_region; bilinear from the native grid, per-channel min-max as in
CircuitNet), the placed netlist (nodes = cells and IO terminals with position/size, nets = node lists), and the
label congH = congV = congestion_raw (physical units; the model's sigmoid range covers it: max 0.59).
Prediction = mean of the two output channels, area-averaged back to the native G-cell grid and scored there.
--no-netlist replaces every RGCN stage with the identity: the same Swin/UPerHead/Generator network on the layout
image alone (the paper's layout-only ablation).
"""
import argparse
import gzip
import hashlib
import os
import pickle
import random
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402


def std(a):
    return a if a.max() == a.min() else (a - a.min()) / (a.max() - a.min())


def to256(a):
    return F.interpolate(torch.tensor(a)[None, None].float(), size=(256, 256), mode="bilinear",
                         align_corners=True)[0, 0].numpy()


def export(s, path):
    g = s["g"]
    W, H = s["die_um"]
    cf = g["cell_feat"]
    f = s["feat"]
    nodes = [{"posX": float(c[3] * W - c[1] / 2), "posY": float(c[4] * H - c[2] / 2), "sizeX": float(c[1]),
              "sizeY": float(c[2]), "macro": False} for c in cf]
    order = np.argsort(g["pin_net"], kind="stable")
    pn, pc = g["pin_net"][order], g["pin_cell"][order]
    cuts = np.flatnonzero(np.diff(pn)) + 1
    nets = [list(map(int, x)) for x in np.split(pc, cuts)]
    y = to256(s["y"]).clip(0, None)
    data = {"density": std(to256(f["cell_density"])), "rudy": std(to256(f["rudy"])),
            "macro": (to256(f["macro_region"]) > 0.5).astype(np.float32),
            "graph": {"nodes": nodes, "nets": nets}, "range": (0.0, 0.0, W, H), "congH": y, "congV": y}
    pickle.dump(data, open(path, "wb"))
    lv = np.round(y * 5)
    pickle.dump([lv, lv], open(path[:-4] + "_level.pkl", "wb"))


class NoGNN(nn.Module):
    def forward(self, graph, h, idx):
        return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="reviewed checkout of lanchengzou/congPred")
    ap.add_argument("--data", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--no-netlist", action="store_true")
    ap.add_argument("--train-frac", type=float, default=1.0)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    torch.manual_seed(a.seed); np.random.seed(a.seed); random.seed(a.seed)
    sys.path.insert(0, a.src)
    import dgl                                                                  # noqa: F401
    from dgl.dataloading import GraphDataLoader
    import dataViG
    from LayNet import LayNet
    src_sha = {f: hashlib.sha256(open(os.path.join(a.src, f), "rb").read()).hexdigest() for f in ("LayNet.py", "dataViG.py")}
    names = bench.designs(a.data)
    cells = bench.cells_of(a.data, names)
    tr, va, te = bench.split(names, cells)
    tr = bench.subsample(tr, cells, a.train_frac)
    t0 = time.time()
    os.makedirs(os.path.join(a.cache, "designs"), exist_ok=True)
    feats = {}
    for n in tr + te:
        p = os.path.join(a.cache, "designs", n + ".pkl")
        if not os.path.isfile(p[:-4] + "_vig.pkl"):
            export(bench.load(a.data, n), p)
            r = dataViG.loadFeat(p)                                             # official preprocessing
            pickle.dump(r[:7], open(p[:-4] + "_vig.pkl", "wb"))
        feats[n] = pickle.load(open(p[:-4] + "_vig.pkl", "rb"))

    def pack(ns, tag):
        path = os.path.join(a.out, f"{tag}ViG.pkl")
        cols = list(zip(*(feats[n] for n in ns)))
        pickle.dump([list(c) for c in cols], open(path, "wb"))
        return dataViG.ViGSet(path)

    os.makedirs(a.out, exist_ok=True)
    train, test = pack(tr, "train"), pack(te, "test")
    print(f"data ready {time.time() - t0:.0f}s: {len(train)} train / {len(test)} test", flush=True)
    dev = torch.device(a.device)
    model = LayNet(chIn=5, chOut=2, chMid=16, img_size=256, patch_size=4, embed_dim=32, window_size=7, mlp_ratio=2.,
                   drop_rate=0., attn_drop_rate=0., drop_path_rate=0.2, depths=[2, 2, 4, 2], num_heads=[2, 4, 8, 16],
                   out_indices=(0, 1, 2, 3))
    if a.no_netlist:
        model.vit.gnns = nn.ModuleList([NoGNN() for _ in model.vit.gnns])
    model.to(dev)
    import train_laynet_shim as shim                                            # PixelContrastiveLoss, verbatim
    L2, CL1, CL2 = nn.MSELoss(), shim.PixelContrastiveLoss(), shim.PixelContrastiveLoss()
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    sch = torch.optim.lr_scheduler.StepLR(opt, step_size=max(1, a.epochs // 4), gamma=0.5)
    loader = GraphDataLoader(train, batch_size=a.batch, shuffle=True, num_workers=4)
    for ep in range(a.epochs):
        model.train()
        tot = []
        for bx, by in loader:
            bx = bx.to(dev)
            by = torch.as_tensor(by, dtype=torch.float).to(dev)
            pred, y = model(bx)
            loss = L2(pred, by[:, :2]) + 1e-6 * (CL1(y[:, :32], by[:, 2]) + CL2(y[:, 32:], by[:, 3]))
            opt.zero_grad(); loss.backward(); opt.step()
            tot.append(loss.item())
        sch.step()
        if ep % 25 == 24:
            print(f"epoch {ep + 1} loss {np.mean(tot):.6f} {time.time() - t0:.0f}s", flush=True)
    model.eval()
    per = {}
    raw = {n: bench.load(a.data, n)["y"] for n in te}
    with torch.no_grad():
        for i, n in enumerate(te):
            hg, _ = test[i]
            pred, _ = model(dgl.batch([hg]).to(dev))
            p = F.interpolate(pred.mean(1, keepdim=True), size=raw[n].shape, mode="area")[0, 0].cpu().numpy()
            per[n] = bench.score(p, raw[n])
    for f in ("trainViG.pkl", "testViG.pkl"):
        os.remove(os.path.join(a.out, f))
    bench.write_result(a.out, "laynet_nonetlist" if a.no_netlist else "laynet", a.seed, per,
                       {"n_train": len(tr), "n_test": len(te), "epochs": a.epochs, "batch": a.batch,
                        "params": sum(p.numel() for p in model.parameters()), "upstream_sha256": src_sha,
                        "wall_s": round(time.time() - t0, 1), "train_frac": a.train_frac})


if __name__ == "__main__":
    main()
