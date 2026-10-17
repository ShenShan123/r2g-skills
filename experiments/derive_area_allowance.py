#!/usr/bin/env python3
"""Derive the per-platform area allowance from the preflight, don't hand-pick it.

The die is sized from the PILOT's recorded cell area, but the pilot ran a
DIFFERENT period -- one flat value per platform. The frozen spec gives each size
class its own, and a tighter period makes the resizer insert timing-repair
buffers, so the final layout's cell area exceeds the pilot's by a factor that
depends on how much that design's period moved. Measured on the preflight, the
extra area IS the buffers: on gf180's C03211 they are 39% of the final cell
area, on C00496 27%, on C03488 24%.

Wherever the period tightened, the die is therefore undersized and the achieved
utilization overshoots the frozen target. That matters because a frozen die
turns the overshoot into congestion, and congestion is the one symptom r2g
cannot repair once the die is fixed (its only lever lowers utilization). Cost
without signal -- the same test every other parameter in this spec was set by.

The allowance is computed from designs where utilization actually BINDS. A
PDN-floored design's ratio is noise: its die comes from the floor, so the cell
area never decided anything.

Output is JSON that make_spec_table.py reads, so the number is never
hand-copied. Re-running the preflight updates it.
"""
import argparse
import glob
import json
import statistics
from pathlib import Path

D = Path("/proj/workarea/yangao")
PLATS = ("nangate45", "gf180", "sky130hd")


def metrics(rd: Path) -> dict:
    for f in glob.glob(str(rd / "logs" / "*" / "*" / "*" / "6_report.json")):
        try:
            return json.load(open(f))
        except Exception:
            pass
    return {}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(D / "spec/area_allowance.json"))
    ap.add_argument("--min-samples", type=int, default=4,
                    help="refuse to emit an allowance from fewer binding designs")
    args = ap.parse_args(argv)

    res, report = {}, {}
    for plat in PLATS:
        spec = json.load(open(D / ("spec/spec_%s.json" % plat)))
        root = D / "_preflight" / plat / "runs"
        if not root.is_dir():
            print("%-10s 没有预检产出，跳过" % plat)
            continue
        ratios, floored, buf_share = [], 0, []
        for rd in sorted(p for p in root.iterdir() if p.is_dir()):
            name = "__".join(rd.name.split("__")[:-2])
            e = spec.get(name) or {}
            if not e:
                continue
            if e.get("pdn_floored"):
                floored += 1
                continue                       # its die came from the floor
            m = metrics(rd)
            pilot = e.get("pilot_cell_area_um2")
            inst = m.get("finish__design__instance__area")
            if not pilot or not inst:
                continue
            ratios.append(inst / pilot)
            b = m.get("finish__design__instance__area__class:timing_repair_buffer") or 0
            buf_share.append(b / inst if inst else 0)
        if len(ratios) < args.min_samples:
            print("%-10s 只有 %d 份可用（下限 %d），不输出余量"
                  % (plat, len(ratios), args.min_samples))
            report[plat] = {"samples": len(ratios), "allowance": None,
                            "reason": "样本不足"}
            continue
        med = statistics.median(ratios)
        res[plat] = round(med, 3)
        report[plat] = {
            "samples": len(ratios), "pdn_floored_skipped": floored,
            "allowance": round(med, 3),
            "ratio_min": round(min(ratios), 3), "ratio_max": round(max(ratios), 3),
            "buffer_share_median": round(statistics.median(buf_share), 3),
        }
        print("%-10s 受约束的 %d 份（跳过 PDN 兜底 %d 份）  面积倍率 中位 %.3f "
              "[%.2f, %.2f]  buffer 占比中位 %.1f%%"
              % (plat, len(ratios), floored, med, min(ratios), max(ratios),
                 100 * statistics.median(buf_share)))

    out = {"_meta": {"source": "_preflight (利用率预检)",
                     "derived_from": "finish__design__instance__area / pilot_cell_area_um2",
                     "note": "只用利用率真正起作用的设计；PDN 兜底的不算",
                     "detail": report},
           **res}
    Path(args.out).write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print("\n写入 %s" % args.out)
    if not res:
        print("没有任何平台得出余量 -- 不要据此改规格表")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
