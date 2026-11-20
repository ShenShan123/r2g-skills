#!/usr/bin/env python3
"""Analytical floor: least-squares map of RUDY and pin RUDY (raw, not log) to the label on the training gcells,
same split and metrics as train_mm.py. Writes OUT/metrics.json in the same format (model 'rudy_fit')."""
import argparse
import json
import os

import numpy as np

from train_mm import design_metrics, load_design, split, ssim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--split-seed", type=int, default=20261118)
    a = ap.parse_args()
    names = sorted(n for n in os.listdir(a.data) if os.path.isfile(os.path.join(a.data, n, "graph.npz")))
    cells = {n: json.load(open(os.path.join(a.data, n, "sample.json")))["cells"] for n in names}
    tr, va, te = split(names, cells, a.split_seed)

    def xy(n):
        d = load_design(os.path.join(a.data, n))
        f = np.expm1(d["x"])                       # undo load_design's log1p: fit on raw RUDY / pin RUDY
        A = np.stack([f[2].ravel(), f[3].ravel(), np.ones(f[2].size)], 1)
        return A, d["y"][0].ravel(), d["y"][0].shape

    As, ys = zip(*[xy(n)[:2] for n in tr])
    coef, *_ = np.linalg.lstsq(np.concatenate(As), np.concatenate(ys), rcond=None)
    per, P, Y = {}, [], []
    for n in te:
        A, y, shp = xy(n)
        p = np.clip(A @ coef, 0, None)
        m = design_metrics(p, y)
        m["ssim"] = ssim(p.reshape(shp), y.reshape(shp))
        per[n] = m
        P.append(p); Y.append(y)
    P, Y = np.concatenate(P), np.concatenate(Y)
    pooled = 1 - ((Y - P) ** 2).sum() / ((Y - Y.mean()) ** 2).sum()
    os.makedirs(a.out, exist_ok=True)
    json.dump({"model": "rudy_fit", "seed": 0, "coef": coef.tolist(), "pooled_r2": float(pooled), "per_design": per,
               "test": te, "n_train": len(tr)}, open(os.path.join(a.out, "metrics.json"), "w"), indent=1)
    print("rudy_fit: test R2 %.3f pooled %.3f" % (np.nanmean([m["r2"] for m in per.values()]), pooled))


if __name__ == "__main__":
    main()
