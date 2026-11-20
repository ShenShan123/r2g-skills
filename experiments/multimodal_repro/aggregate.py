#!/usr/bin/env python3
"""Aggregate the reproduction study: per-method test metrics (per-design values averaged over seeds, then over
designs, with a design-bootstrap 95% CI), and paired per-design comparisons (Wilcoxon signed-rank, Holm-adjusted).

  aggregate.py RUNS_DIR [--frac 1.0] [--json out.json]

RUNS_DIR holds <method>_f<frac>_s<seed>/metrics.json written by bench.write_result.
"""
import argparse
import glob
import json
import os
from collections import defaultdict

import numpy as np

ORDER = ["rudy_fit", "gpdl", "laynet_nonetlist", "lhnn", "verihgn", "laynet"]
LABEL = {"rudy_fit": "RUDY + pin RUDY (fitted)", "gpdl": "GPDL [CircuitNet]", "laynet_nonetlist": "Lay-Net, layout only",
         "lhnn": "LHNN", "verihgn": "VeriHGN", "laynet": "Lay-Net"}
MODAL = {"rudy_fit": "layout", "gpdl": "layout", "laynet_nonetlist": "layout", "lhnn": "netlist+grid",
         "verihgn": "netlist+grid", "laynet": "layout+netlist"}
KEYS = ["r2", "nrmse", "ssim", "pearson", "spearman", "kendall", "f1_top10"]
PAIRS = [("laynet", "laynet_nonetlist"), ("laynet", "gpdl"), ("verihgn", "gpdl"), ("lhnn", "gpdl"),
         ("verihgn", "laynet"), ("laynet", "rudy_fit")]


def boot(v, n=10000, seed=0):
    v = np.asarray(v, float)
    m = np.random.default_rng(seed).choice(v, size=(n, len(v)), replace=True).mean(1)
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def holm(ps):
    order, adj, run = np.argsort(ps), np.empty(len(ps)), 0.0
    for r, i in enumerate(order):
        run = max(run, min(1.0, (len(ps) - r) * ps[i]))
        adj[i] = run
    return adj


def main():
    from scipy.stats import wilcoxon
    ap = argparse.ArgumentParser()
    ap.add_argument("runs")
    ap.add_argument("--frac", default="1.0")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    per = defaultdict(lambda: defaultdict(list))
    seeds = defaultdict(list)
    for f in glob.glob(os.path.join(a.runs, f"*_f{a.frac}_s*", "metrics.json")):
        d = json.load(open(f))
        seeds[d["method"]].append(d["seed"])
        for n, m in d["per_design"].items():
            per[d["method"]][n].append(m)
    methods = [m for m in ORDER if m in per]
    designs = sorted(set.intersection(*(set(per[m]) for m in methods)))

    def avg(m, k):
        return np.array([np.nanmean([s[k] for s in per[m][n]]) for n in designs])

    res = {"n_test_designs": len(designs), "train_frac": a.frac, "methods": {}}
    print(f"{len(designs)} test designs, train fraction {a.frac}; per-design values averaged over seeds")
    print(f"{'method':28s} {'modality':15s} seeds  R2 [95% CI]            NRMSE  SSIM   Pearson Spearman Kendall F1@10%")
    for m in methods:
        row = {k: float(np.nanmean(avg(m, k))) for k in KEYS}
        row["r2_ci"] = boot(avg(m, "r2"))
        row["seeds"] = sorted(seeds[m])
        res["methods"][m] = row
        print(f"{LABEL[m]:28s} {MODAL[m]:15s} {len(seeds[m]):3d}   {row['r2']:.3f} [{row['r2_ci'][0]:.3f}, {row['r2_ci'][1]:.3f}]"
              f"   {row['nrmse']:.4f} {row['ssim']:.3f}  {row['pearson']:.3f}   {row['spearman']:.3f}    {row['kendall']:.3f}   {row['f1_top10']:.3f}")
    pairs = [(x, y) for x, y in PAIRS if x in per and y in per]
    if pairs:
        ps, rows = [], []
        for x, y in pairs:
            d = avg(x, "r2") - avg(y, "r2")
            ps.append(wilcoxon(d).pvalue if np.any(d != 0) else 1.0)
            rows.append((x, y, float(d.mean()), boot(d), int((d > 0).sum()), int((d < 0).sum())))
        adj = holm(np.array(ps))
        res["paired"] = []
        print(f"\npaired per-design R2 difference (Wilcoxon signed-rank, Holm over {len(pairs)} comparisons)")
        for (x, y, mu, ci, b, w), p in zip(rows, adj):
            res["paired"].append({"a": x, "b": y, "delta_r2": mu, "ci": ci, "p_holm": float(p), "a_better": b, "a_worse": w})
            print(f"  {LABEL[x]:22s} - {LABEL[y]:26s} {mu:+.4f} [{ci[0]:+.4f}, {ci[1]:+.4f}]  p={p:.2g}  better/worse {b}/{w}")
    if a.json:
        json.dump(res, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
