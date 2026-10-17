"""Why does gf180 land 10 points over target while nangate45 lands on it?

The die is sized from the PILOT's recorded cell area at 50%. If the FINAL
layout's cell area systematically exceeds that number on one platform, the
achieved utilization runs over by the same factor -- and the die, being frozen,
cannot absorb it. The class breakdown says what the extra area IS.
"""
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")


def metrics(rd):
    for f in glob.glob(str(rd / "logs" / "*" / "*" / "*" / "6_report.json")):
        try:
            return json.load(open(f))
        except Exception:
            pass
    return {}


for plat in ("nangate45", "gf180"):
    spec = json.load(open(D / ("spec/spec_%s.json" % plat)))
    root = D / "_preflight" / plat / "runs"
    if not root.is_dir():
        continue
    print("=== %s" % plat)
    print("%-42s %9s %9s %6s  %s" %
          ("设计", "试跑面积", "最终面积", "倍率", "最终面积的构成"))
    ratios = []
    for rd in sorted(root.iterdir()):
        if not rd.is_dir():
            continue
        name = "__".join(rd.name.split("__")[:-2])
        e = spec.get(name) or {}
        m = metrics(rd)
        pilot = e.get("pilot_cell_area_um2")
        inst = m.get("finish__design__instance__area")
        if not pilot or not inst:
            continue
        ratios.append(inst / pilot)
        cls = {k.split("class:")[1]: v for k, v in m.items()
               if "instance__area__class:" in k and "fill" not in k}
        top = sorted(cls.items(), key=lambda kv: -kv[1])[:3]
        print("%-42s %9.0f %9.0f %6.2fx  %s"
              % (name[:42], pilot, inst, inst / pilot,
                 ", ".join("%s %.0f" % (a.replace("_cell", ""), b) for a, b in top)))
    if ratios:
        ratios.sort()
        print("   最终/试跑 面积倍率: 中位 %.2fx  最小 %.2fx  最大 %.2fx"
              % (ratios[len(ratios) // 2], ratios[0], ratios[-1]))
    print()
