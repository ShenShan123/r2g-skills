#!/usr/bin/env python3
"""Public FORMAT checker for the image-conversion contract (CONTRACT_IMAGE.md v1.0).

    python3 check_format_image.py <out_dir> [<design_dir>] [--json report.json]

Checks only the form of the output: both archives load without pickle, contain exactly the
contract's array names, every array is float32, 2-D, finite and non-negative, all arrays share
one shape, pin_density is integer-valued, and -- when <design_dir> is given -- that shape is the
(nx, ny) the contract derives from 5_route.def's DIEAREA and GCELLGRID. It never computes a
correct value: passing says the output is well-formed, not that it is right.
Exit code 0 iff every check passes.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np

FEATURES = ["cell_density", "macro_region", "rudy", "pin_rudy", "pin_density"]
LABELS = ["congestion", "congestion_raw"]


def grid_shape(def_path):
    sx = sy = die = None
    with open(def_path, errors="replace") as f:
        for line in f:
            if line.startswith("DIEAREA"):
                v = [int(x) for x in re.findall(r"-?\d+", line)]
                xs, ys = v[0::2], v[1::2]
                die = (min(xs), min(ys), max(xs), max(ys))
            elif line.startswith("GCELLGRID"):
                p = line.split()
                if p[1] == "X":
                    sx = int(p[p.index("STEP") + 1])
                else:
                    sy = int(p[p.index("STEP") + 1])
            elif line.startswith("COMPONENTS"):
                break
    return max(1, math.ceil((die[2] - die[0]) / sx)), max(1, math.ceil((die[3] - die[1]) / sy))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("design_dir", type=Path, nargs="?")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:400]})
        return ok

    arrays = {}
    for fn, names in (("features.npz", FEATURES), ("labels.npz", LABELS)):
        p = a.out_dir / fn
        if not add(f"{fn} exists", p.is_file(), str(p)):
            continue
        try:
            z = np.load(p, allow_pickle=False)
            keys = sorted(z.files)
        except Exception as exc:                       # noqa: BLE001
            add(f"{fn} loads", False, f"{type(exc).__name__}: {exc}")
            continue
        add(f"{fn} has exactly {names}", keys == sorted(names), f"found {keys}")
        for k in names:
            if k in z.files:
                arrays[k] = z[k]
    for k, v in arrays.items():
        add(f"{k} float32", v.dtype == np.float32, v.dtype)
        add(f"{k} 2-D", v.ndim == 2, v.shape)
        if v.ndim == 2 and v.dtype.kind == "f":
            add(f"{k} finite", bool(np.isfinite(v).all()))
            add(f"{k} >= 0", bool((v[np.isfinite(v)] >= 0).all()))
    shapes = {k: v.shape for k, v in arrays.items()}
    add("all arrays share one shape", len(set(shapes.values())) <= 1, shapes)
    if "pin_density" in arrays:
        v = arrays["pin_density"]
        add("pin_density integer-valued", bool(np.all(np.isfinite(v)) and np.all(v == np.round(v))))
    if a.design_dir and arrays:
        exp = grid_shape(a.design_dir / "5_route.def")
        add("shape == (nx, ny) of the contract grid", all(s == exp for s in shapes.values()), f"expected {exp}, got {shapes}")
    ok = all(c["ok"] for c in checks)
    out = {"ok": ok, "n_checks": len(checks), "n_failed": sum(not c["ok"] for c in checks),
           "failed": [c for c in checks if not c["ok"]]}
    if a.json:
        a.json.write_text(json.dumps({**out, "checks": checks}, indent=1))
    print(json.dumps(out, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
