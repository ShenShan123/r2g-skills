#!/usr/bin/env python3
"""Independent verifier for an image dataset built by build_images.py.

Written separately from the builder on purpose: it re-parses the DEF and LEF
with its own small readers and re-derives every channel it can with different
arithmetic (vectorised rectangle overlap instead of the builder's loop), so a
shared mistake would have to be made twice. A plausible-looking image with
wrong values is this dataset's failure mode -- shapes and summary statistics
look fine either way -- so each check compares VALUES.

Checks
  structure     every channel present, shape == grid sidecar, finite, >= 0
  provenance    feature DEF / grid sidecar sha256 match the meta; feature DEF is
                pre-route (no routed wires) and, for place/floorplan, has no fill cells
  cell_density  full array re-derived from COMPONENTS + LEF SIZE (max |diff|)
  macro_region  same for CLASS BLOCK masters
  conservation  sum(cell_density*area) == total std-cell area inside the die
  pin_density   total == signal-net pins with a location (instance + IO)
  rudy          integral == sum over nets of (w+h)/(w*h) * area(box ∩ die)
  pin_rudy      total == sum over pins of their net's (w+h)/(w*h)
  congestion    both label channels == congestion_grid.csv exactly
  graph_join    image congestion averaged over each cell's bbox == the graph
                dataset's per-cell label (cell_congestion.csv), sampled
  ir_drop       channel == per-GCell max of ir_drop.csv; max equal; zero where no cell
  axis          a non-square grid is [x][y]: a cell's own GCell is non-zero

Usage: verify_images.py <images_dir> --labels-dir <labels> --final-def <6_final.def>
                        [--lef ...] [--json out.json]
Exit 0 only if every check passes.
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys

import numpy as np

ROT = {"W", "E", "FW", "FE"}


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


# ---- independent readers -------------------------------------------------------
def read_lef(paths):
    sizes, classes, pins = {}, {}, {}
    for p in paths:
        cur, cur_pin = None, None
        for line in open(p, errors="replace"):
            t = line.replace(";", " ").split()
            if not t:
                continue
            if t[0] == "MACRO":
                cur, cur_pin = t[1], None
                pins[cur] = {}
            elif cur and t[0] == "CLASS" and cur not in classes:
                classes[cur] = t[1].upper()
            elif cur and t[0] == "SIZE" and "BY" in t:
                b = t.index("BY")
                sizes[cur] = (float(t[b - 1]), float(t[b + 1]))
            elif cur and t[0] == "PIN" and len(t) > 1:
                cur_pin = t[1]
                pins[cur][cur_pin] = []
            elif cur and cur_pin and t[0] == "RECT":
                v = [float(x) for x in t[1:] if re.match(r"^-?[\d.]+$", x)][-4:]
                if len(v) == 4:
                    pins[cur][cur_pin].append(((v[0] + v[2]) / 2, (v[1] + v[3]) / 2))
            elif t[0] == "END" and len(t) > 1:
                if cur_pin and t[1] == cur_pin:
                    cur_pin = None
                elif t[1] == cur:
                    cur = None
    return sizes, classes, pins


def read_def(path):
    text = open(path, errors="replace").read()
    dbu = float(re.search(r"UNITS DISTANCE MICRONS (\d+)", text).group(1))
    comps = {}
    sec = re.search(r"^COMPONENTS \d+ ;(.*?)^END COMPONENTS", text, re.S | re.M)
    for rec in (sec.group(1) if sec else "").split(";"):
        m = re.search(r"-\s+(\S+)\s+(\S+).*?\+\s*(PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*(\w+)", rec, re.S)
        if m:
            comps[m.group(1)] = (m.group(2), int(m.group(4)), int(m.group(5)), m.group(6))
    io = {}
    sec = re.search(r"^PINS \d+ ;(.*?)^END PINS", text, re.S | re.M)
    for rec in (sec.group(1) if sec else "").split(";"):
        m = re.search(r"-\s+(\S+).*?\+\s*(?:PLACED|FIXED|COVER)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", rec, re.S)
        if m:
            io[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    nets = []
    sec = re.search(r"^NETS \d+ ;(.*?)^END NETS", text, re.S | re.M)
    for rec in (sec.group(1) if sec else "").split(";"):
        m = re.search(r"-\s+(\S+)", rec)
        if not m:
            continue
        use = re.search(r"\+\s*USE\s+(\w+)", rec)
        conns = re.findall(r"\(\s*(\S+)\s+(\S+)\s*\)", rec.split("+ ROUTED")[0])
        nets.append((m.group(1), use.group(1).upper() if use else "", conns))
    routed = "+ ROUTED" in (sec.group(1) if sec else "")
    return dbu, comps, io, nets, routed


def orient_pt(px, py, o, w, h):
    return {"N": (px, py), "S": (w - px, h - py), "W": (h - py, px), "E": (py, w - px),
            "FN": (w - px, py), "FS": (px, h - py), "FW": (py, px), "FE": (h - py, w - px)}.get(o, (px, py))


# ---- vectorised raster of axis-aligned rectangles -----------------------------
def raster_area(rects, edges_x, edges_y):
    """Exact overlap area of each rect with each GCell, summed: (nx, ny) array."""
    out = np.zeros((len(edges_x) - 1, len(edges_y) - 1))
    if not len(rects):
        return out
    r = np.asarray(rects, dtype=float)
    lo_x, hi_x = edges_x[:-1], edges_x[1:]
    lo_y, hi_y = edges_y[:-1], edges_y[1:]
    for k in range(0, len(r), 4096):
        b = r[k:k + 4096]
        ox = np.clip(np.minimum(b[:, 2:3], hi_x) - np.maximum(b[:, 0:1], lo_x), 0, None)   # (n, nx)
        oy = np.clip(np.minimum(b[:, 3:4], hi_y) - np.maximum(b[:, 1:2], lo_y), 0, None)   # (n, ny)
        out += ox.T @ oy
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("images_dir")
    ap.add_argument("--labels-dir", required=True)
    ap.add_argument("--final-def", required=True, help="the routed DEF the labels came from")
    ap.add_argument("--lef", action="append", default=None)
    ap.add_argument("--json", default="")
    ap.add_argument("--samples", type=int, default=300)
    a = ap.parse_args(argv)

    res = []

    def check(name, ok, detail=""):
        res.append({"check": name, "ok": bool(ok), "detail": str(detail)[:300]})

    def maxdiff(x, y):
        """max |x - y|, or inf when the shapes differ (a transposed channel must
        FAIL the comparison, not crash the verifier before it reports)."""
        x, y = np.asarray(x, float), np.asarray(y, float)
        return float(np.abs(x - y).max()) if x.shape == y.shape else float("inf")

    meta = json.load(open(os.path.join(a.images_dir, "image_meta.json")))
    F = dict(np.load(os.path.join(a.images_dir, "features.npz")))
    L = dict(np.load(os.path.join(a.images_dir, "labels.npz")))
    g = json.load(open(os.path.join(a.labels_dir, "congestion_grid.json")))
    nx, ny = g["gcell_size"]
    dbu_g = float(g["db_units"])
    sx, sy = g["grid_step_dbu"][0] / dbu_g, g["grid_step_dbu"][1] / dbu_g
    x0, y0, x1, y1 = [v / dbu_g for v in g["die_dbu"]]
    ex = np.minimum(x0 + sx * np.arange(nx + 1), x1)
    ey = np.minimum(y0 + sy * np.arange(ny + 1), y1)
    area = np.outer(np.diff(ex), np.diff(ey))

    # structure
    want_f = ["cell_density", "macro_region", "rudy", "pin_rudy", "pin_density"]
    want_l = ["congestion", "congestion_raw", "ir_drop"]
    check("structure.channels", set(F) == set(want_f) and set(L) == set(want_l), "%s | %s" % (sorted(F), sorted(L)))
    bad = [k for k, v in list(F.items()) + list(L.items())
           if v.shape != (nx, ny) or not np.all(np.isfinite(v)) or (v < 0).any()]
    check("structure.shape_finite_nonneg", not bad and list(meta["shape"]) == [nx, ny], bad or (nx, ny))

    # provenance + leakage
    fdef = meta["sources"]["feature_def"]["path"]
    check("provenance.feature_def_sha", sha256(fdef) == meta["sources"]["feature_def"]["sha256"], fdef)
    check("provenance.grid_sha", sha256(os.path.join(a.labels_dir, "congestion_grid.json"))
          == meta["sources"]["grid"]["sha256"])
    lefs = a.lef or meta["sources"]["lefs"]
    sizes, classes, lpins = read_lef(lefs)
    dbu, comps, io, nets, routed = read_def(fdef)
    check("leakage.feature_def_unrouted", not routed, "routed wires present" if routed else "none")
    fills = [c for c in comps.values() if re.match(r"FILL", c[0], re.I)]
    check("leakage.no_fill_before_route", meta["feature_stage"] == "cts" or not fills, "%d fill cells" % len(fills))

    # occupancy channels, re-derived
    std, mac, std_area = [], [], 0.0
    for name, (m, x, y, o) in comps.items():
        if m not in sizes:
            continue
        w, h = sizes[m]
        if o in ROT:
            w, h = h, w
        r = (x / dbu, y / dbu, x / dbu + w, y / dbu + h)
        (mac if classes.get(m) == "BLOCK" else std).append(r)
        if classes.get(m) != "BLOCK":
            cx0, cy0 = max(r[0], x0), max(r[1], y0)
            cx1, cy1 = min(r[2], x1), min(r[3], y1)
            std_area += max(0, cx1 - cx0) * max(0, cy1 - cy0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cd = np.where(area > 0, raster_area(std, ex, ey) / area, 0)
        mr = np.where(area > 0, raster_area(mac, ex, ey) / area, 0)
    d = maxdiff(cd, F["cell_density"])
    check("cell_density.array", d < 1e-4, "max|diff|=%.2e" % d)
    d = maxdiff(mr, F["macro_region"])
    check("macro_region.array", d < 1e-4, "max|diff|=%.2e over %d macros" % (d, len(mac)))
    tot = float((F["cell_density"].astype(float) * area).sum()) if F["cell_density"].shape == area.shape else float("inf")
    check("conservation.cell_area", abs(tot - std_area) <= 1e-4 * max(std_area, 1),
          "image %.3f vs cells %.3f um2" % (tot, std_area))

    # pins / RUDY, re-derived with this file's own pin geometry
    def pin_pos(inst, pin):
        if inst == "PIN":
            p = io.get(pin)
            return (p[0] / dbu, p[1] / dbu) if p else None
        c = comps.get(inst)
        if not c:
            return None
        m, x, y, o = c
        rects = lpins.get(m, {}).get(pin)
        if not rects or m not in sizes:
            return (x / dbu, y / dbu)
        px = sum(r[0] for r in rects) / len(rects)
        py = sum(r[1] for r in rects) / len(rects)
        qx, qy = orient_pt(px, py, o, sizes[m][0], sizes[m][1])
        return (x / dbu + qx, y / dbu + qy)

    n_pins, rudy_int, pin_rudy_tot = 0, 0.0, 0.0
    for _name, use, conns in nets:
        if use in ("POWER", "GROUND"):
            continue
        pts = [p for p in (pin_pos(i, q) for i, q in conns) if p is not None]
        if len(pts) < 2:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        w, h = max(max(xs) - min(xs), sx), max(max(ys) - min(ys), sy)
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        dens = (w + h) / (w * h)
        bx0, by0 = max(cx - w / 2, x0), max(cy - h / 2, y0)
        bx1, by1 = min(cx + w / 2, x1), min(cy + h / 2, y1)
        rudy_int += dens * max(0, bx1 - bx0) * max(0, by1 - by0)
        inside = [p for p in pts if x0 <= p[0] <= x1 and y0 <= p[1] <= y1]
        n_pins += len(inside)
        pin_rudy_tot += dens * len(inside)
    got = float(F["pin_density"].sum())
    check("pin_density.total", abs(got - n_pins) < 0.5, "image %d vs pins %d" % (got, n_pins))
    got = float((F["rudy"].astype(float) * area).sum()) if F["rudy"].shape == area.shape else float("inf")
    check("rudy.integral", abs(got - rudy_int) <= 1e-3 * max(rudy_int, 1), "image %.3f vs nets %.3f" % (got, rudy_int))
    got = float(F["pin_rudy"].astype(float).sum())
    check("pin_rudy.total", abs(got - pin_rudy_tot) <= 1e-3 * max(pin_rudy_tot, 1),
          "image %.3f vs pins %.3f" % (got, pin_rudy_tot))

    # labels: congestion exact
    cg = np.zeros((nx, ny)); cr = np.zeros((nx, ny))
    for row in csv.DictReader(open(os.path.join(a.labels_dir, "congestion_grid.csv"))):
        cg[int(row["x_gcell"]), int(row["y_gcell"])] = float(row["gaussian_util"])
        cr[int(row["x_gcell"]), int(row["y_gcell"])] = float(row["util"])
    d1 = maxdiff(cg, L["congestion"]); d2 = maxdiff(cr, L["congestion_raw"])
    check("congestion.equals_grid_csv", d1 < 1e-6 and d2 < 1e-6, "max|diff| %.1e / %.1e" % (d1, d2))

    # labels: join with the graph dataset's per-cell congestion label
    cc = os.path.join(a.labels_dir, "cell_congestion.csv")
    if os.path.isfile(cc):
        fdbu, fcomps, _, _, _ = read_def(a.final_def)
        rows = [r for r in csv.DictReader(open(cc)) if r["Cell"] in fcomps]
        random.Random(7).shuffle(rows)
        stepx, stepy = g["grid_step_dbu"]
        worst, n = 0.0, 0
        for r in rows[:a.samples]:
            m, x, y, o = fcomps[r["Cell"]]
            if m not in sizes:
                continue
            w, h = sizes[m][0] * fdbu, sizes[m][1] * fdbu
            if o in ROT:
                w, h = h, w
            gx0, gx1 = min(max(int(x) // stepx, 0), nx - 1), min(max(int(x + w) // stepx, 0), nx - 1)
            gy0, gy1 = min(max(int(y) // stepy, 0), ny - 1), min(max(int(y + h) // stepy, 0), ny - 1)
            if L["congestion"].shape != (nx, ny):
                worst = float("inf")
                break
            v = float(L["congestion"][gx0:gx1 + 1, gy0:gy1 + 1].astype(float).mean())
            worst = max(worst, abs(v - float(r["cell_congestion"])))
            n += 1
        check("graph_join.cell_congestion", n > 0 and worst < 1e-5, "%d cells, max|diff|=%.2e" % (n, worst))

    # labels: IR drop
    irp = os.path.join(a.labels_dir, "ir_drop.csv")
    if os.path.isfile(irp):
        ir = np.zeros((nx, ny)); seen = np.zeros((nx, ny), bool)
        for r in csv.DictReader(open(irp)):
            if str(r.get("has_irdrop", "true")).lower() != "true":
                continue
            x, y = float(r["X"]), float(r["Y"])
            if not (x0 <= x <= x1 and y0 <= y <= y1):
                continue
            i, j = min(int((x - x0) // sx), nx - 1), min(int((y - y0) // sy), ny - 1)
            ir[i, j] = max(ir[i, j], float(r["IR_Drop_mV"])); seen[i, j] = True
        d = maxdiff(ir, L["ir_drop"])
        check("ir_drop.array", d < 1e-3, "max|diff|=%.2e mV" % d)
        check("ir_drop.zero_where_no_cell", L["ir_drop"].shape == seen.shape
              and float(np.abs(L["ir_drop"][~seen]).max(initial=0)) == 0.0)

    # axis: on a non-square grid a transposed array would fail the shape check;
    # on a square one, a cell's own GCell must carry density
    probe = [c for c in std[:200]]
    hits = 0
    for (rx0, ry0, rx1, ry1) in probe:
        i = min(int(((rx0 + rx1) / 2 - x0) // sx), nx - 1)
        j = min(int(((ry0 + ry1) / 2 - y0) // sy), ny - 1)
        hits += F["cell_density"].shape == (nx, ny) and F["cell_density"][i, j] > 0
    check("axis.cells_land_in_own_gcell", not probe or hits == len(probe), "%d/%d" % (hits, len(probe)))

    ok = all(r["ok"] for r in res)
    out = {"status": "ok" if ok else "invalid", "passed": sum(r["ok"] for r in res), "total": len(res),
           "results": res}
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    for r in res:
        print("  [%s] %s  %s" % ("PASS" if r["ok"] else "FAIL", r["check"], r["detail"]))
    print("== images: %d/%d checks passed ==" % (out["passed"], out["total"]))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
