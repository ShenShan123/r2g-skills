"""Are nangate45's DRC stalls and LVS timeouts the same designs?

I wrote that "both checkers fail to reach a verdict on large designs" and called
it the same shape. That is an observation about two numbers, not a demonstrated
connection. If the two sets are the same designs, one cause (size) explains
both. If they are disjoint, they are two independent problems and must be
counted and fixed separately.
"""
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

drc_bad, lvs_bad, both_ok, cells = set(), set(), set(), {}
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    rd = Path(f).parent
    k = "__".join(rd.name.split("__")[:-2])
    if k not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    cells[k] = d.get("cells") or 0
    if d.get("drc_status") in ("timeout", "drc_failed"):
        drc_bad.add(k)
    if d.get("lvs_status") in ("timeout", "lvs_failed"):
        lvs_bad.add(k)
    if d.get("drc_status") == "clean" and d.get("lvs_status") == "clean":
        both_ok.add(k)

inter = drc_bad & lvs_bad
print("DRC 无判决 %d 份   LVS 无判决 %d 份" % (len(drc_bad), len(lvs_bad)))
print("两者交集 %d 份" % len(inter))
print("  只有 DRC 无判决: %d   只有 LVS 无判决: %d"
      % (len(drc_bad - lvs_bad), len(lvs_bad - drc_bad)))


def med(s):
    v = sorted(cells[k] for k in s if k in cells)
    return v[len(v) // 2] if v else 0


print("\n单元数中位:")
print("  两者都无判决   %7d" % med(inter))
print("  只 DRC 无判决  %7d" % med(drc_bad - lvs_bad))
print("  只 LVS 无判决  %7d" % med(lvs_bad - drc_bad))
print("  双清的         %7d" % med(both_ok))

# is size the whole story? if so there is a clean threshold
allk = [(cells[k], k) for k in cells]
allk.sort()
bad = drc_bad | lvs_bad
print("\n按规模排序，看有没有干净的分界:")
for lo, hi in ((0, 10000), (10000, 30000), (30000, 40000), (40000, 60000),
               (60000, 10**9)):
    sub = [k for c, k in allk if lo <= c < hi]
    if not sub:
        continue
    nb = sum(1 for k in sub if k in bad)
    print("  %6d-%-8s 共 %4d 份   无判决 %4d (%3d%%)"
          % (lo, hi if hi < 10**9 else "inf", len(sub), nb,
             100 * nb // len(sub)))
