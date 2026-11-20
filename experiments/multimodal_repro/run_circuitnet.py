#!/usr/bin/env python3
"""GPDL (Liu et al., DATE 2021) as released in the CircuitNet model zoo (circuitnet/CircuitNet @41ade1d7e913,
routability_ir_drop_prediction/models/gpdl.py, vendored verbatim in upstream/circuitnet/), trained with CircuitNet's congestion recipe on the R2G set.

  run_circuitnet.py --data DIR --seed S --out OUT [--iters 10000] [--train-frac 1.0]

Unchanged from CircuitNet: the model class and its init, the 3 input channels (macro_region, RUDY, pin RUDY),
resize to 256x256 with scipy.ndimage.zoom(order=3) and per-channel min-max (generate_training_set.py),
AdamW(lr 2e-4, wd 0), MSE, batch 16, horizontal flip p=0.5, one cosine period down to 1e-7, last checkpoint.
Changed, and why: the label stays in physical units (CircuitNet min-max normalises it per design, which no metric
can invert); max_iters 200k -> --iters (our train split is ~200 designs, so 10k iterations is already ~800 epochs).
Prediction is area-averaged back to each design's native G-cell grid and scored there.
"""
import argparse
import math
import os
import random
import sys
import time

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402


def std(a):
    return a if a.max() == 0 else (a - a.min()) / (a.max() - a.min())


def prep(s):
    from scipy import ndimage
    z = lambda a: ndimage.zoom(a, (256 / a.shape[0], 256 / a.shape[1]), order=3)   # noqa: E731
    x = np.stack([std(z(s["feat"][k])) for k in ("macro_region", "rudy", "pin_rudy")]).astype(np.float32)
    y = np.clip(z(s["y"]), 0, None)[None].astype(np.float32)
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--iters", type=int, default=10000)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--train-frac", type=float, default=1.0)
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    torch.manual_seed(a.seed); np.random.seed(a.seed); random.seed(a.seed)
    from upstream.circuitnet.gpdl import GPDL                                             # noqa: E402
    names = bench.designs(a.data)
    cells = bench.cells_of(a.data, names)
    tr, va, te = bench.split(names, cells)
    tr = bench.subsample(tr, cells, a.train_frac)
    t0 = time.time()
    raw = {n: bench.load(a.data, n) for n in tr + te}
    X, Y = zip(*(prep(raw[n]) for n in tr))
    X, Y = torch.tensor(np.stack(X)), torch.tensor(np.stack(Y))
    model = GPDL(in_channels=3, out_channels=1)
    model.init_weights()
    model.to(a.device)
    opt = torch.optim.AdamW(model.parameters(), lr=2e-4, betas=(0.9, 0.999), weight_decay=0)
    model.train()
    it, order = 0, []
    while it < a.iters:
        if len(order) < a.batch:
            order += random.sample(range(len(tr)), len(tr))
        idx, order = order[:a.batch], order[a.batch:]
        x, y = X[idx].clone(), Y[idx].clone()
        flip = torch.rand(len(idx)) < 0.5                                     # mmcv horizontal flip = width axis
        x[flip], y[flip] = x[flip].flip(-1), y[flip].flip(-1)
        lr = 1e-7 + 0.5 * (2e-4 - 1e-7) * (1 + math.cos(math.pi * it / a.iters))
        for g in opt.param_groups:
            g["lr"] = lr
        loss = F.mse_loss(model(x.to(a.device)), y.to(a.device))
        opt.zero_grad(); loss.backward(); opt.step()
        it += 1
        if it % 1000 == 0:
            print(f"iter {it} loss {loss.item():.6f} {time.time() - t0:.0f}s", flush=True)
    model.eval()
    per = {}
    with torch.no_grad():
        for n in te:
            x, _ = prep(raw[n])
            p = model(torch.tensor(x)[None].to(a.device))
            p = F.interpolate(p, size=raw[n]["y"].shape, mode="area")[0, 0].cpu().numpy()
            per[n] = bench.score(p, raw[n]["y"])
    bench.write_result(a.out, "gpdl", a.seed, per, {"n_train": len(tr), "n_test": len(te), "iters": a.iters,
                                                     "params": sum(p.numel() for p in model.parameters()),
                                                     "wall_s": round(time.time() - t0, 1), "train_frac": a.train_frac})


if __name__ == "__main__":
    main()
