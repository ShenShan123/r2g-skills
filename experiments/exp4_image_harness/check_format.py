#!/usr/bin/env python3
"""Public image FORMAT checker. No physical reference values are computed.

Usage: python3 check_format.py OUTPUT_DIR [DESIGN_DIR] [--json REPORT.json]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from zipfile import BadZipFile

import numpy as np

FEATURES = ("cell_density", "macro_region", "rudy", "pin_rudy", "pin_density")
LABELS = ("congestion", "congestion_raw")


def grid_shape(def_path: Path) -> tuple[int, int]:
    header = []
    with def_path.open() as stream:
        for line in stream:
            if re.match(r"\s*COMPONENTS\s", line):
                break
            header.append(line)
    text = "".join(header)
    points = re.findall(r"\(\s*(-?\d+)\s+(-?\d+)\s*\)",
                        re.search(r"\bDIEAREA\b(.*?);", text, re.S).group(1))
    xs, ys = zip(*((int(x), int(y)) for x, y in points))
    steps = {axis: int(step) for axis, step in re.findall(
        r"\bGCELLGRID\s+([XY])\s+[-\d]+\s+DO\s+\d+\s+STEP\s+(\d+)\s*;", text)}
    return (max(1, (max(xs) - min(xs) + steps["X"] - 1) // steps["X"]),
            max(1, (max(ys) - min(ys) + steps["Y"] - 1) // steps["Y"]))


def check_format(out_dir: Path, design_dir: Path | None = None) -> dict:
    checks = []
    arrays = {}

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)[:400]})

    for filename, names in (("features.npz", FEATURES), ("labels.npz", LABELS)):
        try:
            with np.load(out_dir / filename, allow_pickle=False) as archive:
                add(filename + " exact channels", sorted(archive.files) == sorted(names), archive.files)
                for name in names:
                    if name in archive.files:
                        arrays[name] = archive[name]
        except (OSError, ValueError, TypeError, BadZipFile) as exc:
            add(filename + " loads", False, exc)
    for name, value in arrays.items():
        add(name + " float32", value.dtype == np.float32, value.dtype)
        add(name + " two dimensional", value.ndim == 2, value.shape)
        if value.dtype.kind == "f":
            add(name + " finite", np.isfinite(value).all())
            add(name + " nonnegative", (value >= 0).all())
        if name == "pin_density" and value.dtype.kind in "fiu":
            add(name + " integer valued", np.isfinite(value).all() and (value == np.round(value)).all())
    shapes = {name: value.shape for name, value in arrays.items()}
    add("all seven channels present", len(arrays) == len(FEATURES) + len(LABELS))
    add("common nonempty grid", len(set(shapes.values())) == 1 and
        all(len(shape) == 2 and min(shape) > 0 for shape in shapes.values()), shapes)
    if design_dir is not None:
        try:
            expected = grid_shape(design_dir / "6_final.def")
            add("shape matches routed DEF grid", bool(shapes) and all(
                shape == expected for shape in shapes.values()), {"expected": expected, "actual": shapes})
        except (OSError, ValueError, AttributeError, KeyError, ZeroDivisionError) as exc:
            add("routed DEF grid loads", False, exc)
    failures = [entry for entry in checks if not entry["ok"]]
    return {"ok": not failures, "n_checks": len(checks), "n_failed": len(failures),
            "failed": failures, "checks": checks}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("design_dir", type=Path, nargs="?")
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    report = check_format(args.out_dir, args.design_dir)
    if args.json:
        args.json.write_text(json.dumps(report, indent=2))
    print(json.dumps({key: value for key, value in report.items() if key != "checks"}, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
