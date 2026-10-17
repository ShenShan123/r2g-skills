"""Measure what the frozen spec actually achieved, per design.

Reports, for each design: the spec's die, the pilot's cell count, the new run's
cell count, and achieved utilization from ORFS's own metrics. The verdict that
matters is the spread of achieved utilization against the 50% target -- a median
near 50 means the die is right; a median well above means the frozen SDC inflates
the layout past what the die was sized for, and some designs will not fit.
"""
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
TARGET = 50.0


def metrics(run_dir):
    for f in glob.glob(str(Path(run_dir) / "logs" / "*" / "*" / "*" / "6_report.json")):
        try:
            return json.load(open(f))
        except Exception:
            pass
    return None


print("目标利用率 %.0f%%\n" % TARGET)
print("%-11s %-46s %7s %7s %8s %8s %7s"
      % ("平台", "设计", "试跑单元", "新单元", "core um2", "单元um2", "实际util"))
allu = {}
for plat in ("nangate45", "gf180", "sky130hd"):
    spec = json.load(open(D / ("spec/spec_%s.json" % plat)))
    root = D / "_preflight" / plat / "runs"
    if not root.is_dir():
        print("  %s (无产出)" % plat)
        continue
    us = []
    for rd in sorted(root.iterdir()):
        if not rd.is_dir():
            continue
        name = "__".join(rd.name.split("__")[:-2])
        res = rd / "result.json"
        r = json.load(open(res)) if res.is_file() else {}
        m = metrics(rd) or {}
        core = m.get("finish__design__core__area")
        inst = m.get("finish__design__instance__area")
        u = (100.0 * inst / core) if (core and inst) else None
        if u is not None:
            us.append(u)
        print("%-11s %-46s %7s %7s %8s %8s %7s"
              % (plat, name[:46], (spec.get(name) or {}).get("cells", "?"),
                 r.get("cells", "?"),
                 ("%.0f" % core) if core else "-",
                 ("%.0f" % inst) if inst else "-",
                 ("%.1f%%" % u) if u is not None else (r.get("status") or "?")))
    if us:
        us.sort()
        allu[plat] = us
    print()

print("=== 判决")
for plat, us in allu.items():
    med = us[len(us) // 2]
    over = sum(1 for x in us if x > TARGET + 5)
    print("  %-10s 量到 %d 份  实际util 中位 %.1f%%  最小 %.1f%%  最大 %.1f%%"
          % (plat, len(us), med, us[0], us[-1]))
    print("            超过目标+5%% 的: %d 份%s"
          % (over, "   <- die 偏小，需要重新定" if over > len(us) // 3 else ""))
if not allu:
    print("  没量到任何一份 -- 预检本身失败，不要据此下结论")
