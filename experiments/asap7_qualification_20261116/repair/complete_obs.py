"""complete_obs.py -- add the V1 and M2 shapes each ASAP7 standard cell carries in its GDS but omits from its LEF
abstract as LEF OBS, so the router keeps its required spacing to them (KLayout script; run in the r2g-orfs image).

  klayout -b -rd GDS=<cell gds> -rd LEF_IN=<cell lef> -rd LEF_OUT=<out lef> -rd REPORT=<json> -r complete_obs.py

Why: V1.S.1 markers pair a router VIA12 with a V1 cut INSIDE a cell (e.g. FAx1: 11 V1 cuts in GDS, OBS only on M1).
The router cannot respect metal it is not told about. GDS layers: M1 19/0, M2 20/0, V1 21/0 (ASAP7).
Only OBS is added (never PIN geometry); the cell GDS, the DRC deck and the netlist are unchanged.
A coordinate check confirms LEF and GDS share the cell origin (M1 pin rects from the LEF must lie on GDS M1).
"""
import json
import re

import pya

LAYERS = {"M2": 20, "V1": 21}
ly = pya.Layout()
ly.read(GDS)
dbu = ly.dbu
lef = open(LEF_IN).read()
rep = {"cells_patched": {}, "origin_check": {}}


def gds_rects(cell, layer_no):
    out = []
    for li in ly.layer_indexes():
        if ly.get_info(li).layer == layer_no and ly.get_info(li).datatype == 0:
            it = cell.begin_shapes_rec(li)
            while not it.at_end():
                b = it.shape().bbox().transformed(it.trans())
                out.append((b.left * dbu, b.bottom * dbu, b.right * dbu, b.top * dbu))
                it.next()
    return out


def m1_region(cell):
    r = pya.Region()
    for li in ly.layer_indexes():
        if ly.get_info(li).layer == 19 and ly.get_info(li).datatype == 0:
            r.insert(cell.begin_shapes_rec(li))
    return r


out_parts, pos = [], 0
for m in re.finditer(r"^MACRO (\S+)\n(.*?)^END \1\n", lef, re.S | re.M):
    name, body = m.group(1), m.group(2)
    out_parts.append(lef[pos:m.start()])
    pos = m.end()
    cell = ly.cell(name)
    if cell is None:
        out_parts.append(m.group(0))
        continue
    # origin check on the first M1 pin rect
    pr = re.search(r"PIN \S+.*?LAYER M1 ;\s*RECT ([-\d. ]+);", body, re.S)
    if pr and name not in rep["origin_check"]:
        x0, y0, x1, y1 = map(float, pr.group(1).split())
        box = pya.Box(int(x0 / dbu), int(y0 / dbu), int(x1 / dbu), int(y1 / dbu))
        rep["origin_check"][name] = (pya.Region(box) - m1_region(cell)).is_empty()   # pin rect lies on GDS M1
    obs = re.search(r"\n(\s*)OBS\n(.*?)\n\s*END\n", body, re.S)
    have = set(re.findall(r"LAYER (\S+) ;", obs.group(2))) if obs else set()
    add = []
    for lname, lno in LAYERS.items():
        if lname in have:
            continue
        rs = gds_rects(cell, lno)
        if rs:
            add.append("    LAYER %s ;\n" % lname + "".join("      RECT %.4f %.4f %.4f %.4f ;\n" % r for r in rs))
            rep["cells_patched"].setdefault(name, {})[lname] = len(rs)
    if add:
        block = "".join(add)
        if obs:
            body = body[:obs.end(2)] + "\n" + block.rstrip("\n") + body[obs.end(2):]
        else:
            body = body.rstrip("\n") + "\n  OBS\n" + block + "  END\n"
    out_parts.append("MACRO %s\n%s\nEND %s\n" % (name, body.rstrip("\n"), name))
out_parts.append(lef[pos:])
open(LEF_OUT, "w").write("".join(out_parts))
rep["n_cells_patched"] = len(rep["cells_patched"])
rep["origin_check_all_ok"] = all(rep["origin_check"].values())
json.dump(rep, open(REPORT, "w"), indent=1)
print("patched", rep["n_cells_patched"], "cells; origin check ok:", rep["origin_check_all_ok"],
      "over", len(rep["origin_check"]), "cells")
