#!/usr/bin/env python3
"""Aggregate the multimodal study: per-model test metrics (per-design values averaged over seeds), paired comparisons,
and a size-stratified breakdown.

  aggregate.py RUNS_DIR [--ref exchange] [--json out.json]
"""
import argparse
import glob
import json
import os
from collections import defaultdict

import numpy as np
from scipy.stats import wilcoxon

MODELS = ["rudy_fit", "unet", "unet_mst", "gnn", "graft", "exchange", "graft_mst"]
LABEL = {"rudy_fit": "RUDY (fitted)", "unet": "U-Net (image)", "gnn": "GNN (netlist)",
         "graft": "Graft (Lay-Net-style)", "exchange": "Exchange", "unet_mst": "U-Net + tree maps", "graft_mst": "Graft + tree splat (ours)"}


def boot_ci(v, n=10000, seed=0):
    v = np.asarray(v, float)
    rng = np.random.default_rng(seed)
    m = rng.choice(v, size=(n, len(v)), replace=True).mean(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def holm(ps):
    order = np.argsort(ps)
    adj = np.empty(len(ps))
    run = 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        adj[i] = run
    return adj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--ref", default="exchange")
    ap.add_argument("--json", default="")
    ap.add_argument("--data", default="/home/yangao/mm_data")
    a = ap.parse_args()
    per = defaultdict(lambda: defaultdict(list))          # model -> design -> [metric dicts over seeds]
    pooled = defaultdict(list)
    for f in glob.glob(os.path.join(a.runs, "*", "metrics.json")):
        d = json.load(open(f))
        pooled[d["model"]].append(d["pooled_r2"])
        for n, m in d["per_design"].items():
            per[d["model"]][n].append(m)
    models = [m for m in MODELS if m in per]
    designs = sorted(set.intersection(*(set(per[m]) for m in models)))
    cells = {n: json.load(open(os.path.join(a.data, n, "sample.json")))["cells"] for n in designs}

    def avg(m, key):
        return np.array([np.nanmean([s[key] for s in per[m][n]]) for n in designs])

    res = {"n_test_designs": len(designs), "models": {}}
    print(f"{len(designs)} test designs; per-design values averaged over seeds")
    print(f"{'model':24s} seeds  R2 mean [95% CI]        median  pooled  NRMSE  SSIM   F1     AUC")
    for m in models:
        r2 = avg(m, "r2")
        lo, hi = boot_ci(r2)
        row = {"seeds": len(pooled[m]), "r2_mean": float(r2.mean()), "r2_ci": [lo, hi], "r2_median": float(np.median(r2)),
               "pooled_r2": float(np.mean(pooled[m])), "nrmse": float(avg(m, "nrmse").mean()),
               "ssim": float(avg(m, "ssim").mean()), "f1": float(avg(m, "f1_top10").mean()),
               "auc": float(np.nanmean(avg(m, "auc_top10")))}
        res["models"][m] = row
        print(f"{LABEL[m]:24s} {row['seeds']:3d}  {row['r2_mean']:.3f} [{lo:.3f}, {hi:.3f}]   {row['r2_median']:.3f}"
              f"   {row['pooled_r2']:.3f}  {row['nrmse']:.4f} {row['ssim']:.3f} {row['f1']:.3f} {row['auc']:.3f}")
    ref = a.ref
    if ref in per:
        others = [m for m in models if m != ref]
        rr = avg(ref, "r2")
        ps, rows = [], []
        for m in others:
            d = rr - avg(m, "r2")
            lo, hi = boot_ci(d)
            p = wilcoxon(d).pvalue if np.any(d != 0) else 1.0
            ps.append(p)
            rows.append((m, float(d.mean()), lo, hi, int((d > 0).sum()), int((d < 0).sum())))
        adj = holm(np.array(ps))
        res["paired"] = []
        print(f"\npaired per-design R2, {LABEL[ref]} minus other (Wilcoxon, Holm over {len(others)} comparisons)")
        for (m, mean, lo, hi, b, w), p in zip(rows, adj):
            res["paired"].append({"other": m, "delta": mean, "ci": [lo, hi], "p_holm": float(p), "better": b, "worse": w})
            print(f"  vs {LABEL[m]:24s} {mean:+.4f} [{lo:+.4f}, {hi:+.4f}]  p={p:.2g}  better/worse {b}/{w}")
    # size strata
    edges = [1000, 5000, 20000, 60001]
    res["by_size"] = {}
    print("\nper-design R2 by cell count (test designs)")
    for lo_, hi_ in zip(edges[:-1], edges[1:]):
        idx = [i for i, n in enumerate(designs) if lo_ <= cells[n] < hi_]
        if not idx:
            continue
        row = {m: float(avg(m, "r2")[idx].mean()) for m in models}
        res["by_size"][f"{lo_}-{hi_ - 1}"] = {"n": len(idx), **row}
        print(f"  {lo_:>5}-{hi_ - 1:<6} n={len(idx):3d}  " + "  ".join(f"{m}={row[m]:.3f}" for m in models))
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
