"""Did the p95 period table actually deliver 95%?

p95 is the 95th percentile of each size class's achievable period -- the
per-design minimum, computed as `period_given - worst_slack` -- so by
construction 95% of designs could meet it UNDER THE CONDITIONS IT WAS MEASURED.
Those conditions were the pilot's: 20% utilization, short wires, small delays.
The frozen spec runs at 50%, where wires are longer and every achievable period
is worse than the number the table was built from.

The wide preflight runs the real combination, so its slack says how much of the
95% survived.
"""
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
for plat in ("nangate45", "gf180", "sky130hd"):
    root = D / "_preflight" / plat / "runs"
    if not root.is_dir():
        continue
    met = missed = nocon = nover = 0
    worst = []
    for f in sorted(glob.glob(str(root / "*" / "result.json"))):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ws = d.get("worst_slack")
        if d.get("status") != "pass":
            nover += 1
            continue
        if not isinstance(ws, (int, float)):
            nocon += 1
            continue
        worst.append(ws)
        if ws >= 0:
            met += 1
        else:
            missed += 1
    tot = met + missed
    if not tot:
        print("%-10s 还没有可用数据" % plat)
        continue
    worst.sort()
    print("%-10s 有时序数据 %d 份：达到周期 %d (%.0f%%)  没达到 %d (%.0f%%)"
          % (plat, tot, met, 100 * met / tot, missed, 100 * missed / tot))
    print("            worst_slack: 最差 %+.3f  中位 %+.3f  最好 %+.3f ns"
          % (worst[0], worst[len(worst) // 2], worst[-1]))
    if nover or nocon:
        print("            （流程未通过 %d 份，无时序数 %d 份，不计入）"
              % (nover, nocon))
