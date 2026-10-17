"""Can the experiment as designed show anything? Four checks on the pilot data.

The question is NOT "how many failed" but "how many failed in a way r2g's
repair arm can act on". A failure pool made mostly of things repair cannot
touch gives a small measured improvement however good the repairs are, and
three days of machine time would not reveal that -- it has to be checked first.

Buckets, by what would have to change to fix them:
  REPAIRABLE   the design/config can be changed: backend aborts (placement,
               PDN, pin assignment), DRC violations, LVS mismatches
  CHECKER      nothing wrong with the design -- the CHECKER failed to reach a
               verdict (timeout, crash). Fixed by better checking (apparatus),
               never by repairing the layout
  FRONTEND     synthesis itself failed, so no layout exists to repair. Belongs
               to the RTL side, not the backend
  UNFIXABLE    congestion-class failures with the die frozen: r2g's only lever
               lowers utilization, which is forbidden once the die is fixed
"""
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
CORP = {"nangate45": "n45_full", "gf180": "gf180_full", "sky130hd": "full_out"}
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}


def key(run_name):
    return "__".join(run_name.split("__")[:-2])


def bucket(d):
    drc, lvs = d.get("drc_status"), d.get("lvs_status")
    if drc == "clean" and lvs == "clean":
        return "CLEAN"
    fs = (d.get("fail_stage") or d.get("orfs_fail_stage") or "")
    fc = (d.get("failure_class") or "")
    st = d.get("status") or ""
    # no layout at all
    if drc in (None, "no_gds") and lvs in (None, "no_gds"):
        if "yosys" in fs or "synth" in fs or "yosys" in fc:
            return "FRONTEND"
        if "route" in fs or "congest" in fc or "grt" in fc.lower():
            return "UNFIXABLE_congestion"
        return "REPAIRABLE_no_layout(%s)" % (fs or st or "?")
    # a layout exists; what blocked the verdict?
    if drc in ("timeout", "drc_failed") or lvs in ("timeout", "lvs_failed"):
        return "CHECKER"
    return "REPAIRABLE_dirty"


print("=== 1. dev 集(1249 份)失败池的构成 —— 按'要改什么才能修'分类\n")
roll = {}
for plat, root in CORP.items():
    rows = []
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if key(Path(f).parent.name) in dev:
            rows.append(d)
    c = collections.Counter(bucket(d) for d in rows)
    roll[plat] = c
    tot = sum(c.values())
    clean = c.get("CLEAN", 0)
    print("  %-10s 受试 %d  双清 %d  失败 %d" % (plat, tot, clean, tot - clean))
    for k, v in c.most_common():
        if k == "CLEAN":
            continue
        print("      %5d  %s" % (v, k))
    print()

print("=== 2. 三平台都在合成阶段失败的设计(语料问题，签核修不了)\n")
fe = {}
for plat, root in CORP.items():
    s = set()
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        k = key(Path(f).parent.name)
        if k in dev and bucket(d) == "FRONTEND":
            s.add(k)
    fe[plat] = s
    print("  %-10s 合成失败 %d 份" % (plat, len(s)))
allthree = set.intersection(*fe.values()) if len(fe) == 3 else set()
print("  三平台都失败: %d 份  <- 这些在任何平台都不可能双清" % len(allthree))

print("\n=== 3. dev 集里没有规格条目的设计(会走 SPEC MISS)\n")
for plat in CORP:
    sk = json.load(open(D / ("spec/spec_%s.skipped.json" % plat)))
    names = sk if isinstance(sk, list) else list(sk)
    inter = len([n for n in names if (n if isinstance(n, str) else n.get("design", "")) in dev])
    print("  %-10s 无规格 %d 份，其中落在 dev 的 %d 份" % (plat, len(names), inter))

print("\n=== 4. 时序是不是通过条件？看试跑里时序违例的设计最终算没算过\n")
for plat, root in CORP.items():
    n_ws = n_ws_clean = 0
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ws = d.get("worst_slack")
        if ws is None or not isinstance(ws, (int, float)):
            continue
        if ws < 0:
            n_ws += 1
            if d.get("drc_status") == "clean" and d.get("lvs_status") == "clean":
                n_ws_clean += 1
    print("  %-10s 时序为负(没满足周期) %d 份，其中仍被记为双清 %d 份"
          % (plat, n_ws, n_ws_clean))
