"""What blocks a verdict on nangate45? 94 dev designs have none.

gf180's answer was per-table execution. nangate45 needs its own: the failure
pool there is the same SHAPE (checker produces no verdict) but the cause may be
different, and a fix for one platform is not a fix for the other.
"""
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

rows = []
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if "__".join(Path(f).parent.name.split("__")[:-2]) in dev:
        rows.append(d)

print("nangate45 dev %d 份\n" % len(rows))
c = collections.Counter((d.get("drc_status"), d.get("lvs_status")) for d in rows)
print("(drc, lvs) 组合:")
for k, v in c.most_common(10):
    print("   %5d  drc=%-12s lvs=%s" % (v, k[0], k[1]))

print("\nDRC 检查器:")
print("  ", collections.Counter(d.get("drc_checker") for d in rows).most_common(4))
print("LVS 检查器:")
print("  ", collections.Counter(d.get("lvs_checker") for d in rows).most_common(4))

for field, label in (("drc", "DRC"), ("lvs", "LVS")):
    bad = [d for d in rows if d.get("%s_status" % field)
           in ("timeout", "%s_failed" % field)]
    if not bad:
        continue
    secs = sorted(d.get("%s_seconds" % field) or 0 for d in bad)
    print("\n%s 无判决 %d 份  用时 中位 %.0fs 最大 %.0fs"
          % (label, len(bad), secs[len(secs) // 2], secs[-1]))
    cells = sorted((d.get("cells") or 0) for d in bad)
    print("   单元数 中位 %d  最小 %d  最大 %d"
          % (cells[len(cells) // 2], cells[0], cells[-1]))
    ok = [d for d in rows if d.get("%s_status" % field) == "clean"]
    if ok:
        oc = sorted((d.get("cells") or 0) for d in ok)
        print("   对照 判出来的 %d 份 单元数中位 %d"
              % (len(ok), oc[len(oc) // 2]))
