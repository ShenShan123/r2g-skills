#!/usr/bin/env python3
"""VeriHGN (Hu et al., KDD 2026) -- official code (SC-Lab-Go/VeriHGN @28a6f1ebbd61, vendored verbatim in
upstream/verihgn/: model, graph builder, loss) trained with its released config (configs/ispd2015.yaml) on R2G.

  run_verihgn.py --data DIR --cache DIR --seed S --out OUT [--train-frac 1.0]

The repository ships no preprocessing, only the sample format its graph builder reads. `export_sample` writes that
format from our placement-stage sample, following the paper's feature definitions (Sec. 3.2):
  cell_features.npy  [C,13]  x, y, w, h, area, #incident nets, #pins, mean / max incident-net HPWL,
                             mean incident-net degree, macro flag, input-pin ratio, master frequency
  net_features.npy   [N,6]   HPWL, bbox dx, bbox dy, degree, log degree, bbox area
  pin_edge_index.npy [2,P]   (cell, net);  pin_features.npy [P,3] drives-net flag, cell offset from the net-bbox
                             centre normalised by the bbox span (x, y)
  geom_edge_*.npy            cell-cell proximity edges, features dx, dy, Manhattan, Euclidean distance
  layout.csv                 per-G-cell RUDY, PinRUDY, MacroRegion, H/V macro margin (the columns the released
                             builder reads) and the label in H_Congestion = V_Congestion = congestion_raw
Choices the release leaves open, and ours: geometric edges = 8 nearest cells by centre distance (the paper does not
give the neighbourhood); cell x, y are expressed in G-cell units and layout.csv x, y are spaced so the builder's
cell->tile mapping lands every cell in the tile that contains it.
One deviation from the released builder, toward the paper: the builder divides log(1+c) by each sample's own maximum,
the paper (Sec. 4.1) by the dataset maximum c_max. We use the paper's (train-split c_max) because only that can be
inverted to score predictions in physical units; the builder's `congestion_cmax` argument is otherwise unused.
Training = released train.py + config: AdamW(5e-4, wd 1e-4), StepLR(50, 0.5), 200 epochs, AMP, grad clip 1.0,
weighted MSE (alpha 10) + variance regularisation (lambda 0.5), early stopping (patience 20) on mean validation
Spearman of the cell and grid heads, test with the best checkpoint. Grid-head predictions are scored.
"""
import argparse
import json
import os
import random
import sys
import time

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402


def macro_margin(macro):
    """H/V macro margin as in Lay-Net's dataViG.cal_macro_margin (non-macro tiles); all-zero without macros."""
    mh, mv = np.zeros(macro.shape), np.zeros(macro.shape)
    if not macro.any():
        return mh, mv
    X, Y = macro.shape
    for x in range(X):
        for y in range(Y):
            if macro[x, y]:
                continue
            L = np.where(macro[x, :y])[0]; R = np.where(macro[x, y + 1:])[0]
            D = np.where(macro[:x, y])[0]; T = np.where(macro[x + 1:, y])[0]
            left = L[-1] if len(L) else 0; right = R[0] + y + 1 if len(R) else Y
            down = D[-1] if len(D) else 0; top = T[0] + x + 1 if len(T) else X
            mh[x, y] = 1 - (right - left) / Y if right != Y or left != 0 else 0
            mv[x, y] = 1 - (top - down) / X if top != X or down != 0 else 0
    return mh, mv


def export_sample(s, out):
    from scipy.spatial import cKDTree
    g = s["g"]
    M, N = s["grid"]
    W, H = s["die_um"]
    cf = g["cell_feat"]
    C = len(cf)
    gx, gy = cf[:, 3] * M, cf[:, 4] * N                          # cell centre in G-cell units
    pc, pn, drv = g["pin_cell"].astype(np.int64), g["pin_net"].astype(np.int64), g["pin_drv"].astype(np.float32)
    nf = g["net_feat"]                                           # degree, dx_um, dy_um
    NN = len(nf)
    hpwl = nf[:, 1] + nf[:, 2]
    # incident-net statistics per cell (unique (cell, net) pairs)
    pairs = np.unique(np.stack([pc, pn], 1), axis=0)
    dnet = np.bincount(pairs[:, 0], minlength=C).astype(np.float32)
    s_h = np.bincount(pairs[:, 0], weights=hpwl[pairs[:, 1]], minlength=C)
    mx_h = np.zeros(C); np.maximum.at(mx_h, pairs[:, 0], hpwl[pairs[:, 1]])
    s_d = np.bincount(pairs[:, 0], weights=nf[pairs[:, 1], 0], minlength=C)
    npin = np.bincount(pc, minlength=C).astype(np.float32)
    nin = np.bincount(pc, weights=1 - drv, minlength=C)
    _, inv, cnt = np.unique(cf[:, 0], return_inverse=True, return_counts=True)
    w, h = cf[:, 1], cf[:, 2]
    cell = np.stack([gx, gy, w, h, w * h, dnet, npin, s_h / np.maximum(dnet, 1), mx_h, s_d / np.maximum(dnet, 1),
                     np.zeros(C), nin / np.maximum(npin, 1), cnt[inv] / C], 1).astype(np.float32)
    net = np.stack([hpwl, nf[:, 1], nf[:, 2], nf[:, 0], np.log(nf[:, 0]), nf[:, 1] * nf[:, 2]], 1).astype(np.float32)
    # pin offset of the cell from its net's bbox centre, in units of the bbox span
    cx, cy = cf[:, 3] * W, cf[:, 4] * H
    lo_x = np.full(NN, np.inf); hi_x = np.full(NN, -np.inf); lo_y = lo_x.copy(); hi_y = hi_x.copy()
    np.minimum.at(lo_x, pn, cx[pc]); np.maximum.at(hi_x, pn, cx[pc])
    np.minimum.at(lo_y, pn, cy[pc]); np.maximum.at(hi_y, pn, cy[pc])
    pin = np.stack([drv, (cx[pc] - (lo_x[pn] + hi_x[pn]) / 2) / np.maximum(hi_x[pn] - lo_x[pn], 1e-3),
                    (cy[pc] - (lo_y[pn] + hi_y[pn]) / 2) / np.maximum(hi_y[pn] - lo_y[pn], 1e-3)], 1).astype(np.float32)
    k = min(8, C - 1)
    pts = np.stack([cx, cy], 1)
    _, nb = cKDTree(pts).query(pts, k + 1)
    src, dst = nb[:, 1:].ravel(), np.repeat(np.arange(C), k)
    d = pts[src] - pts[dst]
    geom = np.stack([d[:, 0], d[:, 1], np.abs(d).sum(1), np.hypot(d[:, 0], d[:, 1])], 1).astype(np.float32)
    os.makedirs(out, exist_ok=True)
    np.save(os.path.join(out, "cell_features.npy"), cell)
    np.save(os.path.join(out, "net_features.npy"), net)
    np.save(os.path.join(out, "pin_edge_index.npy"), np.stack([pc, pn]).astype(np.int64))
    np.save(os.path.join(out, "pin_features.npy"), pin)
    np.save(os.path.join(out, "geom_edge_index.npy"), np.stack([src, dst]).astype(np.int64))
    np.save(os.path.join(out, "geom_edge_features.npy"), geom)
    f = s["feat"]
    mh, mv = macro_margin(f["macro_region"] > 0)
    ii, jj = np.meshgrid(np.arange(M), np.arange(N), indexing="ij")             # x-major rows, as the builder indexes
    sx = M / (M - 1) if M > 1 else 1.0
    sy = N / (N - 1) if N > 1 else 1.0
    import pandas as pd
    pd.DataFrame({"x": (ii * sx).ravel(), "y": (jj * sy).ravel(), "RUDY": f["rudy"].ravel(),
                  "PinRUDY": f["pin_rudy"].ravel(), "MacroRegion": f["macro_region"].ravel(),
                  "H_MacroMargin": mh.ravel(), "V_MacroMargin": mv.ravel(),
                  "H_Congestion": s["y"].ravel(), "V_Congestion": s["y"].ravel()}).to_csv(
        os.path.join(out, "layout.csv"), index=False)
    open(os.path.join(out, "metadata.txt"), "w").write(f"grid_M={M}\ngrid_N={N}\n")


def build(cache, data, n, cmax):
    from upstream.verihgn.graph_builder import build_verihgn_graph
    d = os.path.join(cache, n, "placement_1")
    if not os.path.isfile(os.path.join(d, "metadata.txt")):
        export_sample(bench.load(data, n), d)
    g = build_verihgn_graph(d)
    import pandas as pd
    lay = pd.read_csv(os.path.join(d, "layout.csv"))
    c = ((lay["H_Congestion"].values + lay["V_Congestion"].values) / 2).astype(np.float32)
    gy = torch.from_numpy(np.log1p(c) / cmax)                    # paper's c_max normalisation (see docstring)
    g["grid_l0"].y = gy
    cell_tile = g["cell", "in", "grid_l0"].edge_index[1]
    g["cell"].y = gy[cell_tile]
    g.raw_c = torch.from_numpy(c)
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--epochs", type=int, default=200)
    ap.add_argument("--train-frac", type=float, default=1.0)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    torch.manual_seed(a.seed); np.random.seed(a.seed); random.seed(a.seed)
    from scipy.stats import spearmanr
    from upstream.verihgn.loss import verihgn_loss
    from upstream.verihgn.verihgn import VeriHGN
    names = bench.designs(a.data)
    cells = bench.cells_of(a.data, names)
    tr, va, te = bench.split(names, cells)
    tr = bench.subsample(tr, cells, a.train_frac)
    t0 = time.time()
    cmax = max(float(np.log1p(np.load(os.path.join(a.data, n, "images", "labels.npz"))["congestion_raw"]).max())
               for n in tr)
    G = {n: build(a.cache, a.data, n, cmax) for n in tr + va + te}
    print(f"graphs built {time.time() - t0:.0f}s, c_max={cmax:.4f}", flush=True)
    dev = torch.device(a.device)
    model = VeriHGN(cell_feat_dim=13, net_feat_dim=6, grid_feat_dim=5, pin_feat_dim=3, geom_feat_dim=4,
                    hidden_dim=128, num_layers=3, dropout=0.15, use_checkpoint=True).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-4, weight_decay=1e-4)
    sch = torch.optim.lr_scheduler.StepLR(opt, step_size=50, gamma=0.5)
    scaler = torch.amp.GradScaler("cuda")

    def run(names_, train):
        model.train(train)
        cp, cg, gp, gg = [], [], [], []
        order = names_[:]
        if train:
            random.shuffle(order)
        for n in order:
            d = G[n].to(dev)
            with torch.set_grad_enabled(train), torch.amp.autocast("cuda", enabled=True):
                out = model(d)
                c_pred, g_pred = out["cell_pred"].float(), out["grid_pred"].float()
                L = verihgn_loss(c_pred, d["cell"].y, g_pred, d["grid_l0"].y, alpha=10.0, lambda_var=0.5)
            if train and torch.isfinite(L["total"]):
                opt.zero_grad()
                scaler.scale(L["total"]).backward()
                scaler.unscale_(opt)
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                scaler.step(opt); scaler.update()
            cp.append(c_pred.detach().cpu()); cg.append(d["cell"].y.cpu())
            gp.append(g_pred.detach().cpu()); gg.append(d["grid_l0"].y.cpu())
            G[n] = d.cpu()
        sp = lambda p, t: float(spearmanr(torch.cat(p).numpy(), torch.cat(t).numpy())[0])   # noqa: E731
        return sp(cp, cg), sp(gp, gg)

    best, bad, best_state, log = -1.0, 0, None, []
    for ep in range(1, a.epochs + 1):
        run(tr, True)
        vc, vg = run(va, False)
        sch.step()
        avg = (np.nan_to_num(vc) + np.nan_to_num(vg)) / 2
        log.append({"epoch": ep, "val_cell_spearman": vc, "val_grid_spearman": vg})
        if avg > best:
            best, bad = avg, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= 20:
                break
        if ep % 10 == 0:
            print(f"epoch {ep} val spearman cell {vc:.3f} grid {vg:.3f} best {best:.3f} {time.time() - t0:.0f}s", flush=True)
    model.load_state_dict(best_state)
    model.eval()
    per = {}
    with torch.no_grad():
        for n in te:
            d = G[n].to(dev)
            with torch.amp.autocast("cuda", enabled=True):
                gp = model(d)["grid_pred"].float().cpu().numpy()
            M, N = d.grid_M, d.grid_N
            p = np.expm1(gp * cmax).reshape(M, N)
            per[n] = bench.score(p, d.raw_c.cpu().numpy().reshape(M, N))
    bench.write_result(a.out, "verihgn", a.seed, per, {"n_train": len(tr), "n_test": len(te), "epochs_run": ep,
                                                        "best_val_spearman": best, "c_max": cmax,
                                                        "params": sum(p.numel() for p in model.parameters()),
                                                        "wall_s": round(time.time() - t0, 1), "train_frac": a.train_frac})
    json.dump(log, open(os.path.join(a.out, "train_log.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
