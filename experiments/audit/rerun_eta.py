"""How long will the frozen re-run actually take?

The earlier "2.5-3 days" was extrapolated from a throughput figure. This reads
the pilot's own per-design wall times, which is the only honest basis, and
prices dev (1249 designs) rather than the full corpus.

Two costs, and they do not overlap: the FLOW (synthesis through GDS, one
campaign at --workers 12) and SIGNOFF (DRC + LVS, which the pilot ran as a
separate pass). The DRC half dominates signoff -- the runbook records LVS at 1.6
s against DRC's ~320 s on a small design.
"""
import glob
import json
import statistics
from pathlib import Path

D = Path("/proj/workarea/yangao")
CORP = {"nangate45": "n45_full", "gf180": "gf180_full", "sky130hd": "full_out"}
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
W = 12

print("按试跑实测的单份墙钟，给 dev 的 1249 份定价  (worker=%d)\n" % W)
tot_h = 0.0
for plat, root in CORP.items():
    flow, drc, lvs = [], [], []
    n = 0
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        if "__".join(Path(f).parent.name.split("__")[:-2]) not in dev:
            continue
        try:
            d = json.load(open(f))
        except Exception:
            continue
        n += 1
        for key, acc in (("seconds", flow), ("drc_seconds", drc),
                         ("lvs_seconds", lvs)):
            v = d.get(key)
            if isinstance(v, (int, float)) and v > 0:
                acc.append(v)
    if not flow:
        print("%-10s 没有可用耗时" % plat)
        continue
    # scale each measured sample set up to the full dev count: a design with no
    # recorded time is not a free design
    def hours(samples, label):
        if not samples:
            return 0.0, "(无数据)"
        med = statistics.median(samples)
        total = sum(samples) + med * (n - len(samples))
        return total / W / 3600, ("中位 %.0fs，%d/%d 份有数据"
                                  % (med, len(samples), n))
    fh, fn = hours(flow, "flow")
    dh, dn = hours(drc, "drc")
    lh, ln = hours(lvs, "lvs")
    print("%-10s 受试 %d" % (plat, n))
    print("   flow %6.1f h   %s" % (fh, fn))
    print("   DRC  %6.1f h   %s" % (dh, dn))
    print("   LVS  %6.1f h   %s" % (lh, ln))
    print("   小计 %6.1f h" % (fh + dh + lh))
    tot_h += fh + dh + lh
print("\n三平台串行合计: %.0f 小时 = %.1f 天" % (tot_h, tot_h / 24))
print("（串行是硬规则：三个 campaign 并发就是 load 130 零产出那个状态）")
