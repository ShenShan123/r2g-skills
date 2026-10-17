#!/usr/bin/env python3
"""Per-design frozen spec: the period and die the contract assigns it.

Computed OFFLINE from the pilot's cell count, which breaks a circular
dependency: the period formula takes post-synthesis cell count as input, but
synthesis itself reads the period from the SDC (ABC constrains against it). A
two-pass flow (synthesise at a nominal period, then re-run everything at the
computed one) would cost an extra synthesis per design and still leave the
question of which pass defines the class. Using the pilot count removes both
problems and matches the contract's own rule that a design's size class is
pinned at baseline synthesis and never recomputed -- that rule exists so a
repair cannot shrink its own target.

Honest limit: the pilot count was synthesised at the pilot period, and a tighter
period buffers more, so the count will move (roughly +-20%). Class boundaries
are a decade apart, so that does not cross them.

Period = coefficient * cells^0.25, clamped at the platform's measured p80
ceiling. Exponents fitted on the pilot were 0.243 / 0.240 / 0.262 across the
three platforms -- consistent enough to share 0.25 -- and the clamp matters
because the formula keeps rising while the measured p80 saturates above ~5k
cells: unclamped it would hand a 50k-cell design 2.12 ns where its size peers
demonstrably reach 1.96, i.e. a target looser than the state of the art, which
is how the old constant became vacuous in the first place.
"""
import glob, json, os, sys

D = "/proj/workarea/yangao/"
PLAT = {
    # coefficient, exponent, floor(ns), ceiling(ns), pilot corpus dir, pdn floor(um)
    #
    # BOTH ends are clamped to the measured range, because the whole premise is
    # "what size peers demonstrably achieve" and the fit was anchored on bin
    # MIDPOINTS -- it was never validated at the extremes. Unclamped it asks a
    # 1-cell nangate45 design for 0.142 ns while the 0-100 class's measured p80
    # is 0.30, i.e. twice as fast as its peers; and it keeps rising past 5k
    # cells where the measurement saturates, handing a 50k design 2.12 ns where
    # peers reach 1.96. Either end makes the target something other than the
    # measurement it claims to be.
    #
    # floor   = p80 of the smallest measured class
    # ceiling = largest p80 observed across classes
    "nangate45": (0.142, 0.25, 0.30, 1.95, "n45_full", 32.0),
    "gf180":     (0.818, 0.25, 1.58, 10.30, "gf180_full", 110.0),
    "sky130hd":  (0.534, 0.25, 1.22, 8.80, "full_out", 32.0),
}
# target utilization per size class -- PLACEHOLDER until the sweep lands.
# Keyed (lo, hi) on pilot cell count.
UTIL = {(0, 100): None, (100, 1000): None, (1000, 5000): None,
        (5000, 20000): None, (20000, 10**9): None}

def period_for(cells, coef, expo, lo, hi):
    if cells <= 0:
        return None
    return round(min(max(coef * (cells ** expo), lo), hi), 3)

def main():
    nospec = {}
    util_arg = {}
    for a in sys.argv[1:]:          # e.g. 0-100=55 100-1000=60 ...
        k, v = a.split("=")
        lo, hi = k.split("-")
        util_arg[(int(lo), int(hi) if hi != "inf" else 10**9)] = float(v)
    out = {}
    for plat, (coef, expo, plo, phi, root, floor) in PLAT.items():
        n = 0

        for f in glob.glob(D + root + "/runs/*/result.json"):
            name = os.path.basename(os.path.dirname(f))
            short = "__".join(name.split("__")[:-2])
            try:
                d = json.load(open(f))
            except Exception:
                continue
            c = d.get("cells")
            if not c:
                # No synth_stat: the design did not get far enough in the pilot
                # to have a cell count (166 such on each platform). It has no
                # spec, and is recorded as such rather than given a guessed one
                # -- a design with no spec cannot pass or fail a spec.
                nospec.setdefault(plat, []).append(short)
                continue
            area = d.get("area_um2") or 0.0
            u = None
            for (lo, hi), v in (util_arg or UTIL).items():
                if lo <= c < hi:
                    u = v
                    break
            side = None
            if u and area > 0:
                import math
                side = max(math.sqrt(area / (u / 100.0)), floor)
                side = round(side, 1)
            out.setdefault(plat, {})[short] = {
                "cells": c,
                "period_ns": period_for(c, coef, expo, plo, phi),
                "core_side_um": side,       # None until the sweep fixes util
            }
            n += 1
        print(f"  {plat}: {n} 份")
    for plat, lst in (nospec or {}).items():
        print(f"  {plat}: 无单元数、无规格 {len(lst)} 份")
    p = D + "spec_table.json"
    json.dump(out, open(p, "w"), indent=1)
    print(f"-> {p}")
    # readable sample
    for plat in out:
        rows = sorted(out[plat].items(), key=lambda kv: kv[1]["cells"])
        print(f"\n{plat} 样例 (按单元数):")
        for s, v in [rows[0], rows[len(rows)//4], rows[len(rows)//2],
                     rows[3*len(rows)//4], rows[-1]]:
            print(f"    cells={v['cells']:<8d} period={v['period_ns']:>6.3f} ns"
                  f"  core={v['core_side_um']}")

main()
