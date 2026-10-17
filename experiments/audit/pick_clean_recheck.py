"""Pick designs the MONOLITHIC deck called clean, to recheck with the official
runner.

The hybrid plan (fast deck everywhere, official runner on what it cannot grade)
rests on the two agreeing. The evidence so far is two designs, both DIRTY (1=1
and 2=2). The direction that actually threatens the baseline is the other one:
if the assembled deck MISSES violations the official runner catches, then the
493 gf180 designs reading DRC-clean are not clean and the double-clean count is
wrong.

So this samples CLEAN designs, weighted toward the large end where a deck is
most likely to miss something, and keeps them small enough to grade in about an
hour.
"""
import glob
import json
import random
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
spec = json.load(open(D / "spec/spec_gf180.json"))
random.seed(20261017)

clean = []
for f in glob.glob(str(D / "gf180_full/runs/*/result.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    rn = Path(f).parent.name
    name = "__".join(rn.split("__")[:-2])
    if name not in dev or d.get("drc_status") != "clean":
        continue
    c = (spec.get(name) or {}).get("cells") or 0
    clean.append((c, rn, d.get("drc_seconds") or 0))
clean.sort()
print("dev 的 gf180 里单体 deck 判干净的: %d 份" % len(clean))

# 6 small, 6 medium, 8 large -- the large end is where a miss is most likely
# and also where the official runner is slow, so it is capped
n = len(clean)
small = clean[:n // 3]
med = clean[n // 3:2 * n // 3]
big = clean[2 * n // 3:]
random.shuffle(small); random.shuffle(med)
pick = small[:6] + med[:6] + big[-8:]       # the 8 LARGEST, not random
print("取 %d 份: 小 6 / 中 6 / 最大的 8" % len(pick))
for c, rn, s in pick:
    print("   %6d 单元  单体用时 %5.0fs  %s" % (c, s, rn[:52]))
(D / "_offdrc_clean.txt").write_text("\n".join(rn for _, rn, _ in pick) + "\n")
