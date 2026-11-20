#!/usr/bin/env python3
"""Exp 4 dataset v2: select 300 baseline-signed-off designs per platform (2026-11-07).

Pool: the 1,249 dev designs; a design is eligible on a platform when its frozen baseline run is DRC clean
(drc_status, or a completed 6_drc.lyrdb newer than the GDS) AND LVS clean -- the verdict of record of
experiments/rerun_tally.py, over rerun_nangate45_20261018 / rerun_sky130hd_20261022 / rerun_gf180_20261026.
Size: synthesized cell count (result.json `cells`) in [300, 30000].
Quotas, identical on every platform (GF180 is the binding platform): 300-999 -> 136, 1000-10000 -> 150,
10001-30000 -> 14 (= 300). Within each stratum, designs clean on ALL three platforms first, then the rest;
ties broken by a fixed seed.
"""
import json, random, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
ok = json.load(open(HERE / "pass_cells_verdict_of_record.json"))
STRATA = [("300-999", 300, 999, 136), ("1k-10k", 1000, 10000, 150), ("10k-30k", 10001, 30000, 14)]
SEED = 20261107
common = set.intersection(*[{k for k, v in ok[p].items() if 300 <= v <= 30000} for p in ok])
out, report = {}, {"seed": SEED, "strata": STRATA, "common_eligible": len(common)}
for p in sorted(ok):
    rng = random.Random("%d-%s" % (SEED, p)); sel = []
    for name, lo, hi, q in STRATA:
        pool = sorted(k for k, v in ok[p].items() if lo <= v <= hi)
        a = sorted(k for k in pool if k in common); b = sorted(k for k in pool if k not in common)
        rng.shuffle(a); rng.shuffle(b)
        pick = (a + b)[:q]
        if len(pick) < q: sys.exit("%s %s: only %d of %d" % (p, name, len(pick), q))
        sel += [{"id": k, "cells": ok[p][k], "stratum": name, "clean_on_all_three": k in common} for k in sorted(pick)]
    out[p] = sel
    report[p] = {"n": len(sel), "clean_on_all_three": sum(s["clean_on_all_three"] for s in sel),
                 "median_cells": sorted(s["cells"] for s in sel)[len(sel) // 2]}
ids = [set(s["id"] for s in out[p]) for p in out]
report["same_rtl_on_all_three"] = len(set.intersection(*ids))
json.dump({"selection": out, "report": report}, open(HERE / "selection.json", "w"), indent=1)
print(json.dumps(report, indent=1))
