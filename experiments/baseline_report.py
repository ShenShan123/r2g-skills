#!/usr/bin/env python3
"""The dev baseline as it currently stands, with every caveat attached.

One table the question "what is the baseline for the three platforms" can
actually be answered from. It reports the PILOT's numbers -- the frozen re-run
has not happened -- but applies everything the audit established about how to
count them:

  * verdict of record is each run's result.json, not the aggregate files
  * no verdict (timeout, crashed checker, no layout) is NOT a pass
  * timing is recorded, not judged
  * designs that fail synthesis are reported apart: they can never be
    dual-clean on any platform and signoff-loop has no lever on them
  * the remaining failures are split by WHAT WOULD HAVE TO CHANGE, because
    that, not the raw failure count, is what bounds the measurable improvement
"""
import collections
import glob
import json
from pathlib import Path

D = Path("/proj/workarea/yangao")
CORP = {"nangate45": "n45_full", "gf180": "gf180_full", "sky130hd": "full_out"}
dev = {l.strip() for l in open(D / "splits/dev.txt") if l.strip()}
FRONT = ("yosys", "synth", "parse")


def load(root):
    out = []
    for f in glob.glob(str(D / root / "runs" / "*" / "result.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        k = "__".join(Path(f).parent.name.split("__")[:-2])
        if k in dev:
            d["_k"] = k
            out.append(d)
    return out


def frontend(d):
    blob = (str(d.get("stage") or "") + " " + str(d.get("failure_class") or "")).lower()
    return any(t in blob for t in FRONT)


def bucket(d):
    if d.get("drc_status") == "clean" and d.get("lvs_status") == "clean":
        return "双清"
    if frontend(d):
        return "前端失败(RTL，签核够不着)"
    if d.get("drc_status") in ("timeout", "drc_failed") or \
       d.get("lvs_status") in ("timeout", "lvs_failed"):
        return "检查器无判决(换检查器，不是修设计)"
    if d.get("drc_status") in (None, "no_gds") and d.get("lvs_status") in (None, "no_gds"):
        return "后端崩溃，没出版图(r2g 可修)"
    return "版图脏: DRC/LVS 违例(r2g 可修)"


rows = {p: load(r) for p, r in CORP.items()}
print("=" * 78)
print("dev 集 baseline（试跑参数，冻结重跑尚未进行）   每平台 1249 份")
print("=" * 78)
print()
print("%-11s %7s %9s %9s %8s" % ("平台", "双清", "占全体", "后端分母", "占后端"))
tot_clean = 0
for p, rs in rows.items():
    clean = sum(1 for d in rs if bucket(d) == "双清")
    fe = sum(1 for d in rs if frontend(d))
    be = len(rs) - fe
    tot_clean += clean
    print("%-11s %7d %8.1f%% %9d %7.1f%%"
          % (p, clean, 100 * clean / len(rs), be, 100 * clean / be))
print("%-11s %7d" % ("合计", tot_clean))
print()
print("失败的那些，按'要改什么才能修'拆开：")
print()
order = ["版图脏: DRC/LVS 违例(r2g 可修)", "后端崩溃，没出版图(r2g 可修)",
         "检查器无判决(换检查器，不是修设计)", "前端失败(RTL，签核够不着)"]
print("%-38s %10s %8s %10s" % ("类别", "nangate45", "gf180", "sky130hd"))
r2g_pool = collections.Counter()
for b in order:
    cs = []
    for p in CORP:
        n = sum(1 for d in rows[p] if bucket(d) == b)
        cs.append(n)
        if "r2g 可修" in b:
            r2g_pool[p] += n
    print("%-38s %10d %8d %10d" % (b, *cs))
print()
print("r2g 能发力的池子: %s   合计 %d 份"
      % ("  ".join("%s %d" % (p, n) for p, n in r2g_pool.items()), sum(r2g_pool.values())))
print()
print("注意事项（每条都是实测，不是估计）：")
print("  * 时序不参与判决。sky130hd 有 237 份达不到周期，其中 224 份仍记为双清")
print("  * 55 份设计在三个平台都合成失败，任何参数下都不可能双清")
print("  * nangate45 的 67 份 DRC 无判决全部停在同一处（ANTENNA 段），")
print("    拆开 deck 后它们会变成 clean_except_antenna，而不是 clean")
print("  * gf180 的 86 份 DRC 超时，官方逐表执行已证明能判出其中的大设计")
