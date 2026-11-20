"""Shared benchmark harness for reproducing published congestion predictors on the R2G Nangate45 multimodal set.

Every method sees the same thing: the placement-stage layout (3_place.def -> image channels + netlist graph) of a
SIGNED-OFF design, and predicts the routed congestion of that design's 6_final.def on the same G-cell grid
(`congestion_raw` = max(H demand/capacity, V demand/capacity), physical units, no per-design normalisation).
Every method is scored by the same code (`score`) on the native G-cell grid of each test design, over the same
repository-grouped split (`split`, identical to experiments/multimodal/train_mm.py).

Sample directory layout (built by experiments/multimodal/mm_prep.py):
  <data>/<design>/images/features.npz   cell_density, macro_region, rudy, pin_rudy, pin_density   [nx, ny]
  <data>/<design>/images/labels.npz     congestion_raw                                             [nx, ny]
  <data>/<design>/graph.npz             cells (master, w, h, cx, cy, #pins; die-normalised centres), nets,
                                        pins (cell, net, die-normalised xy, drives-net flag), gcell footprints
  <data>/<design>/labels/congestion_grid.json   die / gcell geometry
"""
from __future__ import annotations

import json
import math
import os
import random

import numpy as np

FEATS = ["cell_density", "macro_region", "rudy", "pin_rudy", "pin_density"]
SPLIT_SEED = 20261118


def designs(data):
    return sorted(n for n in os.listdir(data) if os.path.isfile(os.path.join(data, n, "graph.npz")))


def cells_of(data, names):
    return {n: json.load(open(os.path.join(data, n, "sample.json")))["cells"] for n in names}


def split(names, cells, seed=SPLIT_SEED):
    """repository-grouped, size-stratified 70/15/15 (verbatim from experiments/multimodal/train_mm.py)."""
    repo = {}
    for n in names:
        repo.setdefault(n.split("__")[2], []).append(n)
    groups = sorted(repo.values(), key=lambda g: (-max(cells[n] for n in g), g[0]))
    rng = random.Random(seed)
    tr, va, te = [], [], []
    for i in range(0, len(groups), 20):
        blk = groups[i:i + 20]
        rng.shuffle(blk)
        for j, gp in enumerate(blk):
            (tr if j < 14 else va if j < 17 else te).extend(gp)
    return tr, va, te


def subsample(tr, cells, frac):
    """size-stratified fraction of the train split (for the data-scaling study)."""
    if frac >= 1.0:
        return tr
    return sorted(tr, key=lambda n: cells[n])[::max(1, int(round(1 / frac)))]


def load(data, n):
    F = np.load(os.path.join(data, n, "images", "features.npz"))
    L = np.load(os.path.join(data, n, "images", "labels.npz"))
    G = dict(np.load(os.path.join(data, n, "graph.npz")))
    geo = json.load(open(os.path.join(data, n, "labels", "congestion_grid.json")))
    dbu = float(geo["db_units"])
    die = [v / dbu for v in geo["die_dbu"]]
    return {"feat": {k: F[k].astype(np.float32) for k in FEATS}, "y": L["congestion_raw"].astype(np.float32),
            "g": G, "die_um": (die[2] - die[0], die[3] - die[1]), "grid": tuple(int(v) for v in G["grid"])}


# ------------------------------------------------------------------ scoring
def _ssim(a, b):
    """7x7 uniform-window SSIM, both maps scaled by the label range (as experiments/multimodal/train_mm.py)."""
    from scipy.ndimage import uniform_filter
    rng = max(float(b.max() - b.min()), 1e-6)
    a, b = a.astype(np.float64) / rng, b.astype(np.float64) / rng
    k = min(7, *a.shape)
    f = lambda z: uniform_filter(z, k, mode="reflect")                       # noqa: E731
    mu_a, mu_b = f(a), f(b)
    saa, sbb, sab = f(a * a) - mu_a ** 2, f(b * b) - mu_b ** 2, f(a * b) - mu_a * mu_b
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    m = ((2 * mu_a * mu_b + c1) * (2 * sab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (saa + sbb + c2))
    return float(m.mean())


def score(p, y):
    """per-design metrics of prediction p vs label y (same shape, physical units)."""
    from scipy.stats import kendalltau, pearsonr, spearmanr
    p2, y2 = np.asarray(p, np.float64), np.asarray(y, np.float64)
    p, y = p2.ravel(), y2.ravel()
    ss = ((y - y.mean()) ** 2).sum()
    out = {"r2": float(1 - ((y - p) ** 2).sum() / ss) if ss > 0 else float("nan"),
           "mae": float(np.abs(y - p).mean()), "rmse": float(math.sqrt(((y - p) ** 2).mean())),
           "nrmse": float(math.sqrt(((y - p) ** 2).mean()) / max(y.max() - y.min(), 1e-9)),
           "ssim": _ssim(p2, y2)}
    const = p.std() < 1e-12 or y.std() < 1e-12
    out["pearson"] = float("nan") if const else float(pearsonr(p, y)[0])
    out["spearman"] = float("nan") if const else float(spearmanr(p, y)[0])
    out["kendall"] = float("nan") if const else float(kendalltau(p, y)[0])
    k = max(1, int(round(0.1 * len(y))))
    pos = y >= np.sort(y)[-k]
    ptop = np.zeros_like(pos)
    ptop[np.argsort(p)[-pos.sum():]] = True
    out["f1_top10"] = float(2 * (pos & ptop).sum() / max(pos.sum() + ptop.sum(), 1))
    return out


def write_result(out_dir, method, seed, per_design, extra=None):
    os.makedirs(out_dir, exist_ok=True)
    r = {"method": method, "seed": seed, "split_seed": SPLIT_SEED, "per_design": per_design}
    r.update(extra or {})
    keys = ["r2", "nrmse", "ssim", "pearson", "spearman", "kendall", "f1_top10"]
    r["mean"] = {k: float(np.nanmean([m[k] for m in per_design.values()])) for k in keys}
    json.dump(r, open(os.path.join(out_dir, "metrics.json"), "w"), indent=1)
    print(method, "seed", seed, " ".join(f"{k}={v:.3f}" for k, v in r["mean"].items()), flush=True)
    return r


def resize(a, shape, mode="bilinear"):
    """torch-free resize of a [C,H,W] or [H,W] array (scipy zoom, order 1)."""
    from scipy.ndimage import zoom
    a = np.asarray(a)
    if a.ndim == 2:
        return zoom(a, (shape[0] / a.shape[0], shape[1] / a.shape[1]), order=1 if mode == "bilinear" else 3)
    return np.stack([resize(c, shape, mode) for c in a])
