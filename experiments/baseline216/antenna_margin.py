#!/usr/bin/env python3
"""How far over the limit are the antenna violations r2g cannot repair?

Each KLayout antenna item records its own arithmetic -- #agate, #ametal,
#max_ratio, #ratio, #adiodes -- so the margin is read from the checker's own
numbers rather than recomputed. That matters because the two tools disagree on
the DENOMINATOR, not the limit: the deck's antenna_check(gate, metalN, 300.0,
diode) divides metal area by ONE gate's area, while OpenROAD's per-net PAR
divides by the SUM of gate areas on the net. A net driving k gates therefore
reads k times smaller to OpenROAD, which is why it reports
antenna__violating__nets=0 and inserts 0 diodes on a layout the deck flags.

The margin distribution picks the strategy:
  * tightly clustered just over 300 -> tightening the LEF ANTENNAAREARATIO so
    OpenROAD sees the same marginal nets would let the existing repair act
  * a long tail far over 300 -> the per-gate/per-net definition itself has to be
    reconciled; no ratio setting makes the existing repair reach them
"""
import argparse, collections, glob, json, os, re, sys

VAL = re.compile(r"\[#(\w+)\]\s+(?:float|text):\s*'?([^'<]+)'?")
CAT = re.compile(r"<category>'?([^'<]+?)'?</category>")


def items(path):
    """Yield (category, {field: value}) per <item> in a lyrdb."""
    cat, fields, inside = None, {}, False
    for ln in open(path, errors="replace"):
        if "<item>" in ln:
            cat, fields, inside = None, {}, True
            continue
        if "</item>" in ln:
            if inside and cat:
                yield cat, fields
            inside = False
            continue
        if not inside:
            continue
        m = CAT.search(ln)
        if m:
            cat = m.group(1)
        m = VAL.search(ln)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    rows, bands = [], collections.Counter()
    gates = collections.Counter()
    per_design = collections.Counter()
    for root in a.roots:
        for ly in glob.glob(os.path.join(root, "*", "backend", "RUN_*", "drc", "6_drc.lyrdb")) \
                 + glob.glob(os.path.join(root, "*", "6_drc.lyrdb")):
            des = ly.split(os.sep)[len(root.split(os.sep))]
            for cat, f in items(ly):
                if "ANTENNA" not in cat.upper():
                    continue
                try:
                    r = float(f.get("ratio")); mx = float(f.get("max_ratio"))
                    ag = float(f.get("agate"))
                except (TypeError, ValueError):
                    continue
                over = r / mx
                rows.append({"design": des, "class": cat, "ratio": r, "max": mx,
                             "over": over, "agate": ag,
                             "ametal": f.get("ametal"), "adiodes": f.get("adiodes")})
                per_design[des] += 1
                gates[round(ag, 5)] += 1
                for lo, hi, lab in ((1.0, 1.1, "1.00-1.10x"), (1.1, 1.25, "1.10-1.25x"),
                                    (1.25, 1.5, "1.25-1.50x"), (1.5, 2.0, "1.50-2.00x"),
                                    (2.0, 3.0, "2.00-3.00x"), (3.0, 1e9, ">3x")):
                    if lo <= over < hi:
                        bands[lab] += 1
                        break
    if not rows:
        print("没有带 ratio 字段的天线违例")
        return 1
    rows.sort(key=lambda r: r["over"])
    n = len(rows)
    print(f"残留天线违例 {n} 条，涉及 {len(per_design)} 份设计\n")
    print("超标幅度（ratio / max_ratio）分布:")
    for lab in ("1.00-1.10x", "1.10-1.25x", "1.25-1.50x", "1.50-2.00x",
                "2.00-3.00x", ">3x"):
        if bands[lab]:
            print(f"  {lab:12s} {bands[lab]:5d}  {100*bands[lab]/n:5.1f}%")
    q = lambda f: rows[min(int(n*f), n-1)]["over"]
    print(f"\n分位: p50 {q(.5):.3f}x  p75 {q(.75):.3f}x  p90 {q(.9):.3f}x  "
          f"p99 {q(.99):.3f}x  max {rows[-1]['over']:.3f}x")
    cum = sum(1 for r in rows if r["over"] <= 1.5)
    print(f"\n若把 OpenROAD 的比值阈值收紧到 300/1.5 = 200: "
          f"覆盖 {cum}/{n} 条 ({100*cum/n:.0f}%)")
    print(f"\n门面积取值（分母）: {dict(gates.most_common(5))}")
    print(f"每设计残留条数: 中位 {sorted(per_design.values())[len(per_design)//2]}  "
          f"最大 {max(per_design.values())}")
    if a.json_out:
        json.dump(rows, open(a.json_out, "w"), indent=1)
        print(f"\nwrote {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
