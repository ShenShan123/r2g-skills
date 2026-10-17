"""Pick designs to measure nangate45's DRC wall time on.

Goal: the lowest DRC timeout that drops NO verdict. 67 dev designs stall
forever inside the ANTENNA block and burn the full 7200 s each -- 11 hours of
the re-run's budget at 12 workers -- so a lower bound saves that and costs
nothing, PROVIDED it is above the slowest design that actually finishes.

Design, with the two ways I got measurement design wrong today in mind:

  * only designs that DID get a verdict in the pilot. The stalled ones need no
    re-measuring: all 67 were already shown to stop at the same line, and they
    do not finish at any timeout, so they cannot constrain the bound.
  * weighted to the LARGE end. The bound is set by the slowest finisher, and
    time scales with size; the smallest design tells us nothing. The largest
    design known to stall is 39 782 cells and the smallest, so the interesting
    range is just below that.
  * nangate45 records no drc_seconds, so selection is by size, not by time.
"""
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}

rows = []
for f in glob.glob(str(D / "n45_full/runs/*/result.json")):
    rd = Path(f).parent
    if "__".join(rd.name.split("__")[:-2]) not in dev:
        continue
    try:
        d = json.load(open(f))
    except Exception:
        continue
    if d.get("drc_status") not in ("clean", "violations"):
        continue            # only designs that finished
    if not (rd / "6_final.gds").is_file():
        continue            # DRC needs the GDS
    rows.append(((d.get("cells") or 0), rd.name, d.get("drc_status")))
rows.sort()

print("dev 里有 DRC 判决且留了 GDS 的设计: %d 份" % len(rows))
print("  单元数范围 %d .. %d" % (rows[0][0], rows[-1][0]))

# the slowest finisher sets the bound, so take the top of the range plus a few
# mid-size points to see the trend
pick = rows[-10:] + rows[len(rows) // 2:len(rows) // 2 + 3] + rows[len(rows) // 4:len(rows) // 4 + 2]
seen, out = set(), []
for c, n, st in pick:
    if n not in seen:
        seen.add(n)
        out.append((c, n, st))
out.sort()
print("\n取 %d 份（偏大端，因为上界由最慢的完成者决定）:" % len(out))
for c, n, st in out:
    print("   %7d 单元  判决=%-10s %s" % (c, st, n[:48]))
(D / "_n45timing.select").write_text("\n".join(n for _, n, _ in out) + "\n")
nv = sum(1 for _, _, st in out if st == "violations")
print("\n其中有违例的 %d 份 <- 这是 positive control：" % nv)
print("   如果一份设计秒级退出且 0 违例，它根本没检查，不能当成'快'")
