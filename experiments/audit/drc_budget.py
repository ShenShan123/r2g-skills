"""Size the gf180 DRC budget from the pilot's own timings, not from 4 hard cases.

The official per-table runner is the right checker where the monolithic deck
produces no verdict, and the two were measured to AGREE where both work (1=1 and
2=2 on the two reconciliation designs). That makes a hybrid defensible: the fast
checker everywhere it finishes, the official one on what it cannot grade.
This prices both options.
"""
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

secs, to, n = [], 0, 0
for f in glob.glob(str(D / "gf180_full/runs/*/result.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if "__".join(Path(f).parent.name.split("__")[:-2]) not in dev:
        continue
    st = d.get("drc_status")
    s = d.get("drc_seconds")
    if st == "timeout":
        to += 1
    elif isinstance(s, (int, float)) and s > 0:
        secs.append(s)
    n += 1

secs.sort()
tot = sum(secs)
print("dev 的 gf180 语料 %d 份" % n)
print("  单体 deck 出了判决 %d 份：中位 %.0fs  p90 %.0fs  合计 %.1f 小时"
      % (len(secs), secs[len(secs) // 2], secs[int(len(secs) * .9)], tot / 3600))
print("  单体 deck 超时无判决 %d 份" % to)
print()
# measured on the 4 validation designs
OFF = {"small": 644, "med": 1305, "big": 3891, "huge": 4512}
off_med = 2598        # mean of the four
print("官方逐表实测（4 份难例）: %s" % OFF)
print()
for W in (8, 12, 16, 24):
    hybrid = (tot + to * off_med) / W / 3600
    allo = (n * off_med) / W / 3600
    print("  并发 %2d:  混合方案 %.0f 小时   全部用官方 %.0f 小时" % (W, hybrid, allo))
print()
print("混合 = 全语料跑单体 deck + 只对超时的 %d 份跑官方逐表" % to)
print("两者判决在可比设计上一致，所以混合不是凑合；但它用两个检查器，必须写进契约")
