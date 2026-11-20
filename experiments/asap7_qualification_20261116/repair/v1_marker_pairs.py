"""v1_marker_pairs.py -- classify each V1.S.1 Calibre marker by the two V1 cuts it relates (KLayout batch script).

  klayout -b -rd GDS=<drc.gds> -rd RDB=<drc_results.db> -rd OUT=<json> -r v1_marker_pairs.py

For every V1.S.1 marker polygon, the V1 (21/0) shapes within 30 nm of the marker are collected with their owner:
the standard cell (by cell name) when the cut is inside a placed cell, or ROUTE when it is a top-level / via cell
of the router. Pairs are reported as (owner_a, owner_b) with the master of each, plus whether the two cuts are on
the same instance. Read-only.
"""
import json
import re
from collections import Counter

import pya

ly = pya.Layout()
ly.read(GDS)
top = ly.top_cell()
dbu = ly.dbu
v1 = [li for li in ly.layer_indexes() if ly.get_info(li).layer == 21 and ly.get_info(li).datatype == 0][0]
std = set()
for c in ly.each_cell():
    if c.name.startswith(("AND", "AO", "BUF", "DFF", "DHL", "DLL", "FA", "HA", "HB", "ICG", "INV", "MAJ", "NAND", "NOR",
                          "OA", "OR", "SDF", "TIE", "XNOR", "XOR", "FILL", "TAP", "DECAP", "A2O", "O2A", "ASYNC", "CKINV")):
        std.add(c.cell_index())

# --- parse the ASCII RDB: check name line, then "p <n> <cnt>" followed by cnt coordinate lines
txt = open(RDB).read().splitlines()
prec = float(txt[0].split()[1])
markers, i, cur = [], 1, None
while i < len(txt):
    ln = txt[i]
    if re.match(r"^[A-Za-z]", ln) and not ln.startswith(("p ", "e ", "Rule File")):
        cur = ln.strip()
        i += 1
        continue
    m = re.match(r"^([pe])\s+\d+\s+(\d+)", ln)
    if m and cur == "V1.S.1":
        n = int(m.group(2))
        pts = []
        for k in range(n):                       # p: one x y per line; e: one x1 y1 x2 y2 edge per line
            v = list(map(int, txt[i + 1 + k].split()[:4]))
            pts += [(v[0], v[1])] + ([(v[2], v[3])] if m.group(1) == "e" else [])
        markers.append(pts)
        i += 1 + n
        continue
    i += 1

pairs, owners_all = Counter(), Counter()
examples = []
for pts in markers:
    xs = [p[0] / prec for p in pts]; ys = [p[1] / prec for p in pts]
    box = pya.DBox(min(xs) - 0.03, min(ys) - 0.03, max(xs) + 0.03, max(ys) + 0.03).to_itype(dbu)
    it = top.begin_shapes_rec_touching(v1, box)
    found = []
    while not it.at_end():
        path = it.path()
        owner = "ROUTE"
        for el in path:
            if el.cell_inst().cell_index in std:
                owner = ly.cell(el.cell_inst().cell_index).name
                inst = (el.cell_inst().cell_index, str(el.cell_inst().trans))
                break
        else:
            inst = None
        found.append((owner, inst, it.shape().bbox().transformed(it.trans())))
        it.next()
    kinds = sorted(set((o if o == "ROUTE" else "CELL:" + o) for o, _, _ in found))
    for o, _, _ in found:
        owners_all[o] += 1
    if len(found) >= 2:
        own = sorted(o for o, _, _ in found)[:2]
        cls = ("route-route" if own == ["ROUTE", "ROUTE"] else "route-cell" if "ROUTE" in own else "cell-cell")
        same = len(found) >= 2 and found[0][1] is not None and found[0][1] == found[1][1]
        pairs[(cls, "same-inst" if same else "")] += 1
    else:
        pairs[("lt2-cuts", "")] += 1
    if len(examples) < 12:
        examples.append({"marker_um": [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)],
                         "cuts": [(o, [round(b.left * dbu, 4), round(b.bottom * dbu, 4), round(b.right * dbu, 4), round(b.top * dbu, 4)]) for o, _, b in found]})
out = {"markers": len(markers), "pair_classes": {"|".join(k): v for k, v in pairs.items()},
       "cut_owners": dict(owners_all.most_common(20)), "examples": examples}
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps({k: out[k] for k in ("markers", "pair_classes", "cut_owners")}))
