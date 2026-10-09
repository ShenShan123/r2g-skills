#!/usr/bin/env python3
"""The three-level E1 funnel: front end, back end, signoff.

Attribution is by the stage a design died in, not by its failure class name.
That matters for timeouts: 61 of them stalled in 5_1_grt and belong to the back
end, but two never finished synthesis and belong to the front end -- a blanket
"timeout = back end" rule would misplace those.

A timeout is reported as a censored observation, not a failure: all we know is
that it did not finish inside the 3-hour budget.
"""
import collections, glob, json, os, sys

root = sys.argv[1] if len(sys.argv) > 1 else "/proj/workarea/yangao/full_out"
STAGES = ["1_1_yosys_canonicalize", "1_2_yosys", "1_synth", "2_1_floorplan",
          "2_2_floorplan_macro", "2_3_floorplan_tapcell", "2_4_floorplan_pdn",
          "3_1_place_gp_skip_io", "3_2_place_iop", "3_3_place_gp",
          "3_4_place_resized", "3_5_place_dp", "4_1_cts", "5_1_grt",
          "5_2_route", "5_3_fillcell", "6_1_fill", "6_report"]
FRONT_STAGES = {"1_1_yosys_canonicalize", "1_2_yosys", "1_synth"}
FRONT_CLASS = {"parse_error", "synth_error", "1_1_yosys_canonicalize_error",
               "1_2_yosys_error", "black_box", "missing_top", "ambiguous_top",
               "source_missing", "sdc_error"}

rows = []
for f in glob.glob(os.path.join(root, "runs", "*", "result.json")):
    try: d = json.load(open(f))
    except Exception: continue
    d["_dir"] = os.path.dirname(f)
    rows.append(d)

def reached(d):
    names = {os.path.basename(p)[:-4]
             for p in glob.glob(os.path.join(d["_dir"], "logs", "*", "*", "base", "*.log"))}
    got = [s for s in STAGES if s in names]
    return got[-1] if got else None

def side(d):
    if d.get("status") == "pass":
        return "pass"
    fc = d.get("failure_class") or ""
    if fc in FRONT_CLASS:
        return "front"
    if fc in ("timeout", "oom", "oversize", "aborted_disk"):
        st = d.get("stage") or reached(d)
        return "front" if st in FRONT_STAGES else "back"
    return "back"

tally = collections.Counter(side(d) for d in rows)
n = len(rows)
front, back, ok = tally["front"], tally["back"], tally["pass"]
print(f"=== E1 漏斗 ({n} 个设计) ===\n")
print(f"  输入                 {n:>5}")
print(f"  ├ 前端失败           {front:>5}  ({front/n:>5.1%})")
print(f"  过前端               {n-front:>5}  ({(n-front)/n:>5.1%})")
print(f"  ├ 后端失败           {back:>5}  ({back/n:>5.1%})")
print(f"  过后端(流程完成)     {ok:>5}  ({ok/n:>5.1%})")

sg = [d for d in rows if d.get("drc_status")]
clean = [d for d in sg if d.get("drc_status") == "clean"]
viol = [d for d in sg if d.get("drc_status") == "violations"]
err = [d for d in sg if d.get("drc_status") not in ("clean", "violations")]
print(f"  ├ DRC 已检查         {len(sg):>5}  (未检查 {ok-len(sg)})")
print(f"  ├ DRC 有违例         {len(viol):>5}")
print(f"  ├ DRC 检查失败       {len(err):>5}")
print(f"  过 DRC(干净)         {len(clean):>5}  ({len(clean)/n:>5.1%} of 输入)")

# LVS. Counted over the same flow-complete population as DRC, and kept in three
# buckets -- passed / failed / NOT GRADED -- because a run the checker declined
# to grade is neither of the first two.
LVS_PASS = {"clean", "clean_with_warnings"}
LVS_FAIL = {"mismatch", "pin_mismatch"}   # Netgen reports a swapped connection
                                          # and a wrong drive strength as
                                          # "Top level cell failed pin matching"
lv = [d for d in rows if d.get("lvs_status")]
if lv:
    lpass = [d for d in lv if d.get("lvs_status") in LVS_PASS]
    lfail = [d for d in lv if d.get("lvs_status") in LVS_FAIL]
    lskip = [d for d in lv if d.get("lvs_status") not in LVS_PASS | LVS_FAIL]
    print(f"  ├ LVS 已检查         {len(lv):>5}  (未检查 {ok-len(lv)})")
    print(f"  ├ LVS 失配           {len(lfail):>5}"
          + (f"  {dict(collections.Counter(d['lvs_status'] for d in lfail))}"
             if lfail else ""))
    print(f"  ├ LVS 未判决         {len(lskip):>5}"
          + (f"  {dict(collections.Counter(d['lvs_status'] for d in lskip))}"
             if lskip else "")
          + "   <- 检查器拒绝判定，既非干净也非失配")
    print(f"  过 LVS               {len(lpass):>5}  ({len(lpass)/n:>5.1%} of 输入)")
    both = [d for d in rows
            if d.get("drc_status") == "clean" and d.get("lvs_status") in LVS_PASS]
    print(f"  过 DRC 且过 LVS      {len(both):>5}  ({len(both)/n:>5.1%} of 输入)"
          "   <- 严格签核通过")
else:
    print(f"  ├ LVS                    -  (未运行)")

con = [d for d in rows if d.get("timing_path_constrained") is True]
met = [d for d in con if (d.get("worst_slack") or 0) >= 0]
print(f"\n  时序(仅对有约束路径的设计有意义)")
print(f"    有约束路径         {len(con):>5}")
print(f"    其中达标           {len(met):>5}  ({len(met)/max(1,len(con)):>5.1%})")

print(f"\n=== 损失明细 ===")
for s, label in (("front", "前端"), ("back", "后端")):
    sub = [d for d in rows if side(d) == s]
    print(f"  {label} {len(sub)}:")
    for k, v in collections.Counter(d.get("failure_class") for d in sub).most_common():
        cen = "  (删失: 未在 3 小时预算内完成)" if k == "timeout" else ""
        print(f"     {v:>4}  {k}{cen}")
