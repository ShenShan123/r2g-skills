#!/usr/bin/env python3
"""Non-learned floor: least-squares map of RUDY and pin RUDY (the classic placement-stage estimators, Spindler &
Johannes DATE 2007) to the routed label, fitted on the training G-cells; same split and scoring as every method.

  run_rudy.py --data DIR --out OUT [--train-frac 1.0]
"""
import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bench  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--train-frac", type=float, default=1.0)
    a = ap.parse_args()
    names = bench.designs(a.data)
    cells = bench.cells_of(a.data, names)
    tr, va, te = bench.split(names, cells)
    tr = bench.subsample(tr, cells, a.train_frac)
    S = {n: bench.load(a.data, n) for n in tr + te}
    X = lambda s: np.stack([np.ones(s["y"].size), s["feat"]["rudy"].ravel(), s["feat"]["pin_rudy"].ravel()], 1)  # noqa: E731
    w = np.linalg.lstsq(np.concatenate([X(S[n]) for n in tr]), np.concatenate([S[n]["y"].ravel() for n in tr]), rcond=None)[0]
    per = {n: bench.score((X(S[n]) @ w).clip(0, None).reshape(S[n]["y"].shape), S[n]["y"]) for n in te}
    bench.write_result(a.out, "rudy_fit", 0, per, {"n_train": len(tr), "n_test": len(te), "coef": w.tolist(),
                                                     "train_frac": a.train_frac})


if __name__ == "__main__":
    main()
