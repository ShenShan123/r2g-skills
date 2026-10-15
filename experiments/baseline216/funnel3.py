#!/usr/bin/env python3
"""The three-platform funnel, handoff by handoff, from the ingested campaign.

Every count comes from knowledge_campaign.sqlite (7,491 runs bridged from the
2026-10 campaign), latest run per project, A/B arm dirs excluded. Loss at each
handoff is attributed to the stage that actually stopped the design, not to a
failure-class name, because those two disagree: gf180 recorded 54 designs as
`no_log_harvested` that are the SAME 54 designs reading `synth_error` on the
other two platforms (set intersection 54/54/54).

Two readings are kept separate on purpose:
  * router DRC (detailedroute__route__drc_errors) -- what ORFS itself signs off on
  * signoff DRC (the platform KLayout deck) -- what this project signs off on
They are not the same question, and the gap between them is the point.
"""
import collections, json, sqlite3, sys

D = "/proj/workarea/yangao"
PLATS = ["nangate45", "sky130hd", "gf180"]
FRONTEND = {"parse_error", "1_1_yosys_canonicalize_error",
            "1_2_yosys_error", "synth_error", "no_log_harvested"}


def main():
    db = sys.argv[1] if len(sys.argv) > 1 else f"{D}/knowledge_campaign.sqlite"
    c = sqlite3.connect(db)
    LATEST = ("r.run_id = (SELECT r2.run_id FROM runs r2 "
              "WHERE r2.project_path = r.project_path "
              "ORDER BY r2.ingested_at DESC, r2.run_id DESC LIMIT 1)")
    NOARM = ("r.project_path NOT LIKE '%_abA_%' AND r.project_path NOT LIKE '%_abB_%'")
    out = {}
    for p in PLATS:
        rows = c.execute(
            f"SELECT r.orfs_status, r.orfs_fail_stage, rv.drc_status, "
            f"rv.drc_categories_json, rv.lvs_status, rv.lvs_mismatch_class, r.cell_count "
            f"FROM runs r LEFT JOIN run_violations rv USING(run_id) "
            f"WHERE r.platform=? AND r.ingested_at>'2026-10-01' AND {LATEST} AND {NOARM}",
            (p,)).fetchall()
        f = collections.Counter()
        drc_cls = collections.Counter()
        lvs_cls = collections.Counter()
        stage = collections.Counter()
        f["input"] = len(rows)
        for orfs, fstage, ds, cj, ls, lmc, cells in rows:
            if orfs in ("fail", "tool_crash"):
                st = (fstage or "unknown")
                stage[st] += 1
                if st in ("synth",):
                    f["lost_frontend"] += 1
                else:
                    f["lost_backend"] += 1
                continue
            f["flow_ok"] += 1
            if ds in ("clean", "clean_beol"):
                f["drc_clean"] += 1
            elif ds in ("fail", "failed"):
                f["drc_viol"] += 1
                for k, v in (json.loads(cj or "{}") or {}).items():
                    drc_cls[k] += (v or {}).get("count") or 0
            elif ds in ("stuck", "timeout"):
                f["drc_stuck"] += 1
            else:
                f["drc_none"] += 1
            if ls == "clean":
                f["lvs_clean"] += 1
            elif ls in (None, "skipped"):
                f["lvs_none"] += 1
            else:
                f["lvs_bad"] += 1
                lvs_cls[lmc or ls] += 1
            if ds in ("clean", "clean_beol") and ls == "clean":
                f["both_clean"] += 1
        out[p] = {"f": f, "drc_cls": drc_cls, "lvs_cls": lvs_cls, "stage": stage}

    W = 13
    def row(lab, key, pct_of=None):
        cells = []
        for p in PLATS:
            v = out[p]["f"][key]
            tot = out[p]["f"][pct_of] if pct_of else None
            cells.append(f"{v:5d}" + (f" {100*v/tot:5.1f}%" if tot else "      "))
        print(f"  {lab:<22s}" + "".join(f"{x:>{W}s}" for x in cells))

    print(f"{'':24s}" + "".join(f"{p:>{W}s}" for p in PLATS))
    print("  " + "-" * (22 + W * 3))
    row("输入设计", "input")
    row("流程跑完", "flow_ok", "input")
    row("  前端失败(synth)", "lost_frontend")
    row("  后端失败", "lost_backend")
    print("  " + "-" * (22 + W * 3))
    row("签核 DRC 干净", "drc_clean", "input")
    row("  有违例", "drc_viol")
    row("  超时/卡死", "drc_stuck")
    row("  无判决", "drc_none")
    print("  " + "-" * (22 + W * 3))
    row("LVS 干净", "lvs_clean", "input")
    row("  有问题", "lvs_bad")
    row("  无判决/跳过", "lvs_none")
    print("  " + "=" * (22 + W * 3))
    row("双清(可用于数据集)", "both_clean", "input")
    print()
    for p in PLATS:
        o = out[p]
        print(f"=== {p} ===")
        print(f"  后端失败的阶段: {dict(o['stage'].most_common(6))}")
        if o["drc_cls"]:
            tot = sum(o["drc_cls"].values())
            top = o["drc_cls"].most_common(3)
            print(f"  DRC 违例 {tot} 条, {len(o['drc_cls'])} 类, 头部: "
                  + ", ".join(f"{k}={v}({100*v/tot:.0f}%)" for k, v in top))
        if o["lvs_cls"]:
            print(f"  LVS 问题: {dict(o['lvs_cls'].most_common(4))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
