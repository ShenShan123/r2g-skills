#!/usr/bin/env python3
"""bloat_v1_obs.py IN_LEF OUT_LEF MARGIN_UM CELL[,CELL...] -- grow the V1 OBS rectangles of the named macros by MARGIN.

Why (2026-11-19, v1_marker_pairs.py on 4 v1_obsfull designs, 20/20 markers): after complete_obs.py adds the in-cell
V1 cuts as OBS, every residual V1.S.1 marker pairs a router VIA12 with a cut inside FAx1, placed exactly at the router's
0.018 um cut spacing but offset ~8 nm off the router's via grid (partial projection overlap). The deck wants more than
0.018 um for such pairs; raising V1 SPACING globally (v1_s27) breaks V1.M2.EN.2 everywhere. Growing only those cells'
V1 OBS keeps the router away from their cuts and changes nothing else. Router-input only: GDS, deck, netlist unchanged.
"""
import re
import sys

src, dst, margin, cells = sys.argv[1], sys.argv[2], float(sys.argv[3]), set(sys.argv[4].split(","))
lef = open(src).read()
n_rect = 0


def grow(block):
    global n_rect
    out, layer = [], None
    for ln in block.split("\n"):
        m = re.match(r"\s*LAYER\s+(\S+)\s*;", ln)
        if m:
            layer = m.group(1)
        r = re.match(r"(\s*)RECT\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*;", ln)
        if r and layer == "V1":
            x0, y0, x1, y1 = (float(v) for v in r.groups()[1:])
            ln = "%sRECT %.4f %.4f %.4f %.4f ;" % (r.group(1), x0 - margin, y0 - margin, x1 + margin, y1 + margin)
            n_rect += 1
        out.append(ln)
    return "\n".join(out)


def macro(m):
    name, body = m.group(1), m.group(2)
    if name not in cells:
        return m.group(0)
    o = re.search(r"(\n\s*OBS\n)(.*?)(\n\s*END\n)", body, re.S)
    if not o:
        return m.group(0)
    body = body[:o.start(2)] + grow(o.group(2)) + body[o.end(2):]
    return "MACRO %s\n%sEND %s\n" % (name, body, name)


out = re.sub(r"^MACRO (\S+)\n(.*?)^END \1\n", macro, lef, flags=re.S | re.M)
open(dst, "w").write(out)
print("grew", n_rect, "V1 OBS rects by", margin, "um in", sorted(cells))
