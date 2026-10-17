"""Pick 30 designs for the utilization preflight: 10 per platform, spread over
the four size classes, and all of them designs the PILOT already passed.

The pass-in-pilot filter is the control. The preflight asks one question -- does
the frozen spec's die hold the layout the frozen SDC produces -- and a design
that already failed for an unrelated reason cannot answer it.
"""
import glob
import json
import random
from pathlib import Path

D = Path("/proj/workarea/yangao")
CORP = {"nangate45": "n45_full", "gf180": "gf180_full", "sky130hd": "full_out"}
BINS = [(0, 100), (100, 1000), (1000, 5000), (5000, 10**9)]
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
random.seed(20261017)

out = {}
for plat, root in CORP.items():
    spec = json.load(open(D / ("spec/spec_%s.json" % plat)))
    cand = {i: [] for i in range(len(BINS))}
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        name = "__".join(Path(f).parent.name.split("__")[:-2])
        if name not in dev or name not in spec:
            continue
        # control: the pilot already got this one dual-clean
        if not (d.get("drc_status") == "clean" and d.get("lvs_status") == "clean"):
            continue
        c = spec[name]["cells"]
        for i, (lo, hi) in enumerate(BINS):
            if lo <= c < hi:
                cand[i].append((name, c, spec[name]["die_side_um"]))
                break
    picked = []
    for i in range(len(BINS)):
        random.shuffle(cand[i])
        take = cand[i][:3] if i < 3 else cand[i][:1]
        picked += take
        print("  %-10s 档 %-12s 可选 %4d  取 %d"
              % (plat, "%d-%d" % BINS[i][:2] if BINS[i][1] < 10**9
                 else ">%d" % BINS[i][0], len(cand[i]), len(take)))
    out[plat] = picked
    (D / ("_preflight_%s.select" % plat)).write_text(
        "\n".join(n for n, _, _ in picked) + "\n")

print()
for plat, rows in out.items():
    print("%s (%d 份):" % (plat, len(rows)))
    for n, c, die in rows:
        print("    %-52s %6d 单元  die %sum" % (n[:52], c, die))
