#!/usr/bin/env python3
"""Map KLayout antenna violations back to net names, so the repair can name them.

Used by `diagnose_signoff_fix`'s deck_driven_antenna_diode strategy to turn the
signoff deck's verdict into the net list that orfs_hooks/deck_antenna_diodes.tcl
inserts on. Validated at 8/8 on apb_gpio (8 antenna items -> 5 nets, all APB
top-level ports) and carried a 12-design A/B in which every completed subject
went to zero violations.

`check_antennas -verbose` reports 0 net and 0 pin violations on a layout the
FreePDK45 deck flags 16 times, so OpenROAD's violating set is EMPTY -- it is not
mis-ranking the nets, it cannot see them. Tightening ANTENNAAREARATIO to 200
makes it act but on nets of its own choosing: across 7 paired runs only 1 cleared,
and 3 inserted a diode while keeping every violation. So "which net to repair"
has to come from the deck's verdict rather than from OpenROAD's.

Each lyrdb antenna item carries the offending polygon in micrometres plus the
arithmetic (#agate, #ametal, #ratio). This joins that polygon against the DEF's
per-net routing to recover the net, which is what `repair_antennas -net` needs.
"""
import argparse, collections, json, re, sys

CAT = re.compile(r"<category>'?([^'<]+?)'?</category>")
VAL = re.compile(r"\[#(\w+)\]\s+(?:float|text):\s*'?([^'<]+?)'?\s*$")
POLY = re.compile(r"(?:polygon|edge-pair|edge):\s*\(([^)]*)\)")
NUM = re.compile(r"-?\d+(?:\.\d+)?")


def lyrdb_antennas(path):
    """(category, polygon bbox in um, fields) per antenna item."""
    out, cat, fields, poly, inside = [], None, {}, None, False
    for ln in open(path, errors="replace"):
        if "<item>" in ln:
            cat, fields, poly, inside = None, {}, None, True
            continue
        if "</item>" in ln:
            if inside and cat and "ANTENNA" in cat.upper() and poly:
                out.append((cat, poly, fields))
            inside = False
            continue
        if not inside:
            continue
        m = CAT.search(ln)
        if m:
            cat = m.group(1)
        m = VAL.search(ln.strip())
        if m:
            fields[m.group(1)] = m.group(2)
        m = POLY.search(ln)
        if m and poly is None:
            xs = [float(v) for v in NUM.findall(m.group(1))]
            if len(xs) >= 4:
                px, py = xs[0::2], xs[1::2]
                poly = (min(px), min(py), max(px), max(py))
    return out


def def_net_shapes(path):
    """{net: [(layer, x1,y1,x2,y2 in um)]} from the DEF's NETS routing."""
    units = 1000.0
    nets, cur, inside = collections.defaultdict(list), None, False
    layer, pts = None, []
    for ln in open(path, errors="replace"):
        s = ln.strip()
        if s.startswith("UNITS DISTANCE MICRONS"):
            units = float(s.split()[-2]) if s.split()[-2].isdigit() else units
            continue
        if s.startswith("NETS "):
            inside = True
            continue
        if s.startswith("END NETS"):
            break
        if not inside:
            continue
        if s.startswith("- "):
            cur = s[2:].split()[0]
            layer, pts = None, []
            continue
        if cur is None:
            continue
        # + ROUTED metal4 ( 86265 82740 ) ( 86405 * )
        for tok in re.finditer(r"(?:ROUTED|NEW|FIXED)\s+(\S+)", s):
            layer = tok.group(1)
            pts = []
        # only COORDINATE pairs: a DEF net line also carries connection lists
        # like `( _2600_ A )`, which a loose \S+ pattern happily misreads as a
        # point and then fails to float().
        for m in re.finditer(r"\(\s*(-?\d+(?:\.\d+)?|\*)\s+"
                             r"(-?\d+(?:\.\d+)?|\*)(?:\s+\d+)?\s*\)", s):
            x, y = m.group(1), m.group(2)
            px = pts[-1][0] if (x == "*" and pts) else float(x) / units
            py = pts[-1][1] if (y == "*" and pts) else float(y) / units
            if pts and layer:
                x1, y1 = pts[-1]
                nets[cur].append((layer, min(x1, px), min(y1, py),
                                  max(x1, px), max(y1, py)))
            pts.append((px, py))
    return nets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lyrdb", required=True)
    ap.add_argument("--def", dest="deff", required=True)
    ap.add_argument("--tol", type=float, default=0.3, help="um of slack when matching")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    items = lyrdb_antennas(a.lyrdb)
    nets = def_net_shapes(a.deff)
    print(f"天线违例 {len(items)} 条；DEF 里带布线的网络 {len(nets)} 个")
    hits, miss, per_net = [], 0, collections.Counter()
    for cat, (x1, y1, x2, y2) in ((c, p) for c, p, _ in items):
        want = cat.replace("_ANTENNA", "").lower()          # METAL4_ANTENNA -> metal4
        found = []
        for net, shapes in nets.items():
            for lay, sx1, sy1, sx2, sy2 in shapes:
                if lay.lower() != want:
                    continue
                if (sx1 - a.tol <= x2 and sx2 + a.tol >= x1
                        and sy1 - a.tol <= y2 and sy2 + a.tol >= y1):
                    found.append(net)
                    break
        if found:
            hits.append((cat, sorted(set(found))))
            for n in set(found):
                per_net[n] += 1
        else:
            miss += 1
    print(f"映射成功 {len(hits)}/{len(items)}  未匹配 {miss}")
    print(f"涉及 {len(per_net)} 个不同网络")
    for n, k in per_net.most_common(10):
        print(f"  {k:3d} 条  {n}")
    if a.json_out:
        json.dump({"nets": sorted(per_net), "per_net": dict(per_net),
                   "items": len(items), "mapped": len(hits)},
                  open(a.json_out, "w"), indent=1)
        print(f"wrote {a.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
