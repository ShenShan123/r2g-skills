#!/usr/bin/env python3
"""Emit the frozen per-design spec from the pilot corpus.

One JSON per platform: {design: {cells, period_ns, die_side, core_side}}.
Values follow experiments/BASELINE_PARAMETERS.md exactly; this script is the
single place they are computed, so the baseline run cannot drift from the
document.

Why the PILOT's cell count. The period is a function of post-synthesis size, but
the period is read AT synthesis -- a circular dependency. The contract resolves
it by pinning each design's size class to its BASELINE synthesis and never
recomputing, and the pilot is exactly that: a synthesis measurement at a known
nominal period. Using it is traceable and costs no extra synthesis pass.

Why an explicit die rather than CORE_UTILIZATION. Deriving the die at run time
would let a repair move its own goalpost -- a fix that cuts cell count would
shrink its die and hand itself an easier job. Frozen, a repair that cuts cells
gets more room (a real benefit) and one that grows cells gets squeezed (a real
cost), which is how a tape-out behaves.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import json
import math
import os
from pathlib import Path

# BASELINE_PARAMETERS.md "Clock period - by post-synthesis cell count":
# the pilot's p95 achievable period per size class, rounded up.
PERIOD_NS = {
    "nangate45": [(100, 0.5), (1000, 1.0), (5000, 1.6), (None, 3.0)],
    "gf180":     [(100, 2.2), (1000, 5.0), (5000, 9.0), (None, 16.0)],
    "sky130hd":  [(100, 1.8), (1000, 3.6), (5000, 6.5), (None, 16.0)],
}
# "Utilization - flat 40%" and "PDN core floor - baseline policy"
# 0.50, matching BASELINE_PARAMETERS.md. The anchor is OpenLane's own sky130
# default (FP_CORE_UTIL=50 / PL_TARGET_DENSITY=0.5) rather than the 60-70% the
# literature recommends, because that advice assumes a floorplan the designer
# can still move: here the die is FROZEN, so headroom above the chosen value is
# not a working margin and every congestion failure above it is permanently
# unfixable (r2g's only congestion lever, density_relief, lowers utilization).
# CHANGING THIS REQUIRES REGENERATING THE SPEC TABLE -- it is the one place the
# die is computed, and a stale table would run designs against a die the frozen
# spec no longer describes.
UTILIZATION = 0.50
PDN_FLOOR_UM = {"nangate45": 32.0, "gf180": 110.0, "sky130hd": 32.0}
CORE_INSET_UM = 2.0
PILOT_ROOT = {"nangate45": "n45_full", "gf180": "gf180_full",
              "sky130hd": "full_out"}


def period_for(platform: str, cells: int) -> float:
    for upper, ns in PERIOD_NS[platform]:
        if upper is None or cells < upper:
            return ns
    raise AssertionError("unreachable: last bin has upper=None")


def die_for(platform: str, cell_area_um2: float) -> tuple[float, float]:
    """(die_side, core_side) in um, floored at the platform's PDN minimum."""
    derived = math.sqrt(cell_area_um2 / UTILIZATION) if cell_area_um2 > 0 else 0.0
    core = max(derived, PDN_FLOOR_UM[platform])
    core = math.ceil(core)
    return core + 2 * CORE_INSET_UM, core


def build(platform: str, pilot_dir: Path) -> tuple[dict, dict]:
    spec, skipped = {}, {}
    for f in glob.glob(str(pilot_dir / "runs" / "*" / "result.json")):
        name = os.path.basename(os.path.dirname(f))
        short = "__".join(name.split("__")[:-2])
        try:
            d = json.loads(Path(f).read_text())
        except Exception as exc:
            skipped[short] = f"unreadable result.json: {type(exc).__name__}"
            continue
        cells, area = d.get("cells"), d.get("area_um2")
        # A design the pilot never synthesised has no size, so it has no spec.
        # It still RUNS -- it just inherits the platform's smallest bin and the
        # PDN floor, which is the honest default for an unknown size. Recording
        # it here keeps that visible instead of silent.
        if not cells or not area:
            skipped[short] = f"no pilot size (cells={cells}, area={area})"
            continue
        die, core = die_for(platform, float(area))
        spec[short] = {
            "cells": int(cells),
            "pilot_cell_area_um2": round(float(area), 3),
            "period_ns": period_for(platform, int(cells)),
            "die_side_um": die,
            "core_side_um": core,
            "core_inset_um": CORE_INSET_UM,
            "pdn_floored": core <= PDN_FLOOR_UM[platform],
        }
    return spec, skipped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pilot-root", default="/proj/workarea/yangao",
                    help="directory holding n45_full / gf180_full / full_out")
    ap.add_argument("--out-dir", default="/proj/workarea/yangao/spec")
    ap.add_argument("--platforms", default="nangate45,gf180,sky130hd")
    args = ap.parse_args(argv)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for plat in args.platforms.split(","):
        pilot = Path(args.pilot_root) / PILOT_ROOT[plat]
        if not pilot.is_dir():
            print(f"{plat}: 缺 {pilot}，跳过")
            continue
        spec, skipped = build(plat, pilot)
        floored = sum(1 for v in spec.values() if v["pdn_floored"])
        periods = sorted({v["period_ns"] for v in spec.values()})
        # Provenance, so a consumer can refuse a table that no longer describes
        # the frozen spec. The re-run chain's preflight compares `utilization`
        # against the value it is about to run with and will not start on a
        # mismatch -- without this stamp a table built at 40% would silently
        # hand out 40%-derived dies to a 50% run, and the dies are the whole
        # point of the table (they are explicit, so nothing downstream
        # recomputes them).
        spec["_meta"] = {
            "utilization": UTILIZATION,
            "core_inset_um": CORE_INSET_UM,
            "pdn_floor_um": PDN_FLOOR_UM[plat],
            "platform": plat,
            "designs": len(spec),
            "pdn_floored": floored,
            "skipped_no_pilot_size": len(skipped),
            "periods": periods,
            "pilot_root": str(pilot),
            "generated": _dt.date.today().isoformat(),
        }
        p = out / f"spec_{plat}.json"
        p.write_text(json.dumps(spec, indent=1, sort_keys=True) + "\n")
        print(f"{plat}: {len(spec) - 1} 份 -> {p}")
        print(f"   周期取值 {periods}   被 PDN 下限兜底 {floored} 份"
              f"   无试跑尺寸(跳过) {len(skipped)} 份")
        if skipped:
            (out / f"spec_{plat}.skipped.json").write_text(
                json.dumps(skipped, indent=1, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
