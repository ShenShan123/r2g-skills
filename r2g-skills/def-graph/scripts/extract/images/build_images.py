#!/usr/bin/env python3
"""Image (feature-map) dataset for one design: CircuitNet-style GCell rasters.

The graph dataset puts features and labels on nodes and edges; this one puts
them on the GCell grid, as multi-channel images a CNN can take directly.

Grid. Taken from the congestion label's own sidecar (labels/congestion_grid.json,
written by extract_congestion.py from the routed DEF's GCELLGRID), so every
channel here is pixel-aligned with the congestion label by construction --
there is one grid definition, not two that could drift. Arrays are [x][y],
shape (num_gcells_x, num_gcells_y): CircuitNet's convention and the sidecar's.

Features (inputs) are computed from ONE pre-route DEF only -- by default the
placement DEF (3_place.def), optionally CTS or floorplan -- so no channel can
see routing. The script refuses a feature DEF that carries routed wires.

  cell_density   standard-cell area overlapping the GCell / GCell area (0..1)
  macro_region   hard-macro (LEF CLASS BLOCK) area overlapping / GCell area
  rudy           RUDY: each signal net spreads (w+h)/(w*h) uniformly over its
                 pin bounding box (w, h floored at one GCell step), clipped to
                 the die; integrates to (w+h) per net. Units 1/um.
  pin_rudy       each signal-net pin adds its net's (w+h)/(w*h) to its GCell
  pin_density    signal-net pins (instance pins + IO pins) per GCell

Labels (targets) come from the post-route label stage:

  congestion       gaussian-smoothed routed demand/capacity (= congestion_grid.csv
                   `gaussian_util`, the same values the graph label averages)
  congestion_raw   unsmoothed demand/capacity (`util`)
  ir_drop          worst static IR drop (mV) of the cells located in the GCell
                   (labels/ir_drop.csv; GCells without a cell stay 0)

Outputs in <out_dir>: features.npz, labels.npz (one array per channel name),
image_meta.json (grid, channels, units, sources with sha256), png/<channel>.png
previews (y axis up, as a layout viewer shows it).

Usage:
  build_images.py --feature-def 3_place.def --labels-dir labels/ --out-dir images/
                  [--feature-stage place] [--lef ...]   (LEFs default to SC_LEF/ADDITIONAL_LEFS)
"""
import argparse
import csv
import hashlib
import json
import math
import os
import struct
import sys
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
from techlib import def_parse, lef  # noqa: E402

FEATURES = ["cell_density", "macro_region", "rudy", "pin_rudy", "pin_density"]
LABELS = ["congestion", "congestion_raw", "ir_drop"]
UNITS = {"cell_density": "fraction", "macro_region": "fraction", "rudy": "1/um",
         "pin_rudy": "1/um", "pin_density": "pins", "congestion": "demand/capacity",
         "congestion_raw": "demand/capacity", "ir_drop": "mV"}
SKIP_NET_USE = {"POWER", "GROUND"}
ROTATED = {"W", "E", "FW", "FE"}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Grid:
    """GCell grid in um, from the congestion sidecar."""

    def __init__(self, meta):
        self.nx, self.ny = meta["gcell_size"]
        dbu = float(meta["db_units"])
        sx, sy = meta["grid_step_dbu"]
        x0, y0, x1, y1 = meta["die_dbu"]
        self.dbu = dbu
        self.sx, self.sy = sx / dbu, sy / dbu
        self.x0, self.y0, self.x1, self.y1 = x0 / dbu, y0 / dbu, x1 / dbu, y1 / dbu

    def cell_rect(self, i, j):
        """GCell (i, j) clipped to the die, in um."""
        xa = self.x0 + i * self.sx
        ya = self.y0 + j * self.sy
        return (xa, ya, min(xa + self.sx, self.x1), min(ya + self.sy, self.y1))

    def areas(self):
        a = np.zeros((self.nx, self.ny))
        for i in range(self.nx):
            for j in range(self.ny):
                xa, ya, xb, yb = self.cell_rect(i, j)
                a[i, j] = max(0.0, xb - xa) * max(0.0, yb - ya)
        return a

    def index(self, x, y):
        """GCell holding point (x, y) um, or None outside the die."""
        if not (self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1):
            return None
        i = min(int((x - self.x0) // self.sx), self.nx - 1)
        j = min(int((y - self.y0) // self.sy), self.ny - 1)
        return i, j

    def splat(self, arr, rect, value_per_area):
        """Add value_per_area * overlap_area(rect, gcell) to every GCell rect hits."""
        xa, ya, xb, yb = rect
        xa, ya = max(xa, self.x0), max(ya, self.y0)
        xb, yb = min(xb, self.x1), min(yb, self.y1)
        if xb <= xa or yb <= ya:
            return 0.0
        i0 = max(0, int((xa - self.x0) // self.sx))
        i1 = min(self.nx - 1, int((xb - self.x0) // self.sx))
        j0 = max(0, int((ya - self.y0) // self.sy))
        j1 = min(self.ny - 1, int((yb - self.y0) // self.sy))
        total = 0.0
        for i in range(i0, i1 + 1):
            gx0 = self.x0 + i * self.sx
            ox = min(xb, gx0 + self.sx) - max(xa, gx0)
            if ox <= 0:
                continue
            for j in range(j0, j1 + 1):
                gy0 = self.y0 + j * self.sy
                oy = min(yb, gy0 + self.sy) - max(ya, gy0)
                if oy <= 0:
                    continue
                v = value_per_area * ox * oy
                arr[i, j] += v
                total += v
        return total


def footprint(comp, sizes, dbu):
    w, h = sizes.get(comp["master"], (0.0, 0.0))
    if (comp.get("orient") or "N").upper() in ROTATED:
        w, h = h, w
    x, y = comp["x"] / dbu, comp["y"] / dbu
    return (x, y, x + w, y + h)


def assert_pre_route(def_path):
    """A feature DEF must not carry routing: any ROUTED/NEW wire would leak the
    label's source into the inputs."""
    in_nets = False
    with open(def_path, errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("NETS "):
                in_nets = True
            elif s.startswith("END NETS"):
                return
            elif in_nets and (any(k in s for k in ("+ ROUTED", "+ FIXED", "+ COVER", "+ NOSHIELD"))
                              or s.startswith("NEW ")):
                raise SystemExit("REFUSED: %s contains routed wires -- not a pre-route DEF" % def_path)


def build_features(def_path, grid, lef_paths):
    sizes = lef.merge_macro_sizes(lef_paths)
    classes = lef.macro_classes(lef_paths)
    geom = lef.macro_pin_geometry(lef_paths)
    comps = def_parse.parse_components(def_path)
    nets = def_parse.parse_nets(def_path)
    iopins = def_parse.parse_io_pins(def_path)
    dbu = grid.dbu
    area = grid.areas()
    f = {k: np.zeros((grid.nx, grid.ny)) for k in FEATURES}
    stats = {"instances": 0, "unplaced": 0, "unknown_master": 0, "macros": 0,
             "std_area_um2": 0.0, "macro_area_um2": 0.0, "nets": 0, "pins": 0, "hpwl_um": 0.0}

    # occupancy channels: exact overlap area, then normalised by GCell area
    std_area = np.zeros((grid.nx, grid.ny))
    mac_area = np.zeros((grid.nx, grid.ny))
    for name, c in comps.items():
        if c.get("status") not in ("PLACED", "FIXED", "COVER") or "x" not in c:
            stats["unplaced"] += 1
            continue
        if c["master"] not in sizes:
            stats["unknown_master"] += 1
            continue
        stats["instances"] += 1
        r = footprint(c, sizes, dbu)
        a = (r[2] - r[0]) * (r[3] - r[1])
        if classes.get(c["master"]) == "BLOCK":
            stats["macros"] += 1
            stats["macro_area_um2"] += a
            grid.splat(mac_area, r, 1.0)
        else:
            stats["std_area_um2"] += a
            grid.splat(std_area, r, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        f["cell_density"] = np.where(area > 0, std_area / area, 0.0)
        f["macro_region"] = np.where(area > 0, mac_area / area, 0.0)

    # pin positions per signal net
    def pin_xy(inst, pin):
        if inst == "PIN":
            p = iopins.get(pin)
            return (p["x"] / dbu, p["y"] / dbu) if p else None
        c = comps.get(inst)
        if not c or "x" not in c:
            return None
        return lef.pin_abs_pos_um(geom, c["x"] / dbu, c["y"] / dbu, c.get("orient"), c["master"], pin)

    rudy_area = np.zeros((grid.nx, grid.ny))
    min_w, min_h = grid.sx, grid.sy
    for n in nets.values():
        if n.get("use", "").upper() in SKIP_NET_USE:
            continue
        pts = [p for p in (pin_xy(a, b) for a, b in n["conns"]) if p is not None]
        if len(pts) < 2:
            continue
        stats["nets"] += 1
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        xa, xb, ya, yb = min(xs), max(xs), min(ys), max(ys)
        stats["hpwl_um"] += (xb - xa) + (yb - ya)
        # a degenerate (straight / single-point) box still needs routing; floor
        # each side at one GCell step, centred, so the density stays finite
        w, h = max(xb - xa, min_w), max(yb - ya, min_h)
        cx, cy = (xa + xb) / 2.0, (ya + yb) / 2.0
        dens = (w + h) / (w * h)
        grid.splat(rudy_area, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), dens)
        for (x, y) in pts:
            ij = grid.index(x, y)
            if ij is None:
                continue
            f["pin_rudy"][ij] += dens
            f["pin_density"][ij] += 1
            stats["pins"] += 1
    with np.errstate(divide="ignore", invalid="ignore"):
        f["rudy"] = np.where(area > 0, rudy_area / area, 0.0)
    return f, stats


def build_labels(labels_dir, grid):
    lab = {k: np.zeros((grid.nx, grid.ny)) for k in LABELS}
    with open(os.path.join(labels_dir, "congestion_grid.csv"), newline="") as fh:
        for row in csv.DictReader(fh):
            x, y = int(row["x_gcell"]), int(row["y_gcell"])
            if not (0 <= x < grid.nx and 0 <= y < grid.ny):
                raise SystemExit("congestion grid cell (%d,%d) outside %dx%d -- axes swapped?" % (x, y, grid.nx, grid.ny))
            lab["congestion"][x, y] = float(row["gaussian_util"])
            lab["congestion_raw"][x, y] = float(row["util"])
    ir_path = os.path.join(labels_dir, "ir_drop.csv")
    n_ir = 0
    if os.path.isfile(ir_path):
        with open(ir_path, newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("has_irdrop", "true")).lower() != "true":
                    continue
                ij = grid.index(float(row["X"]), float(row["Y"]))
                if ij is None:
                    continue
                v = float(row["IR_Drop_mV"])
                if v > lab["ir_drop"][ij]:
                    lab["ir_drop"][ij] = v
                n_ir += 1
    return lab, {"ir_cells": n_ir, "ir_available": os.path.isfile(ir_path)}


# viridis-like colour stops (dark purple -> teal -> yellow): perceptually ordered,
# readable in print and for most colour-vision deficiencies
_CMAP = np.array([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]], dtype=float)


def write_png(path, arr):
    """Colour-mapped PNG of an [x][y] array, y up, upscaled to >= 256 px.
    Scaled by the 99th percentile of the non-zero values so a few hot GCells do
    not wash the rest out. Pure zlib -- no imaging library needed. (Previews
    only: the numbers live in the .npz.)"""
    a = np.asarray(arr, dtype=float)
    nz = a[a > 0]
    top = float(np.percentile(nz, 99)) if nz.size else 1.0
    top = top if top > 0 else 1.0
    t = np.clip(a / top, 0, 1).T[::-1]                     # rows = y, top row = max y
    pos = t * (len(_CMAP) - 1)
    lo = np.floor(pos).astype(int).clip(0, len(_CMAP) - 2)
    frac = (pos - lo)[..., None]
    rgb = (_CMAP[lo] * (1 - frac) + _CMAP[lo + 1] * frac).astype(np.uint8)
    k = max(1, int(math.ceil(256 / max(t.shape))))
    rgb = rgb.repeat(k, axis=0).repeat(k, axis=1)
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[r].tobytes() for r in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as fh:
        fh.write(png)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--feature-def", required=True, help="pre-route DEF (placement by default)")
    ap.add_argument("--feature-stage", default="place", choices=["floorplan", "place", "cts"])
    ap.add_argument("--labels-dir", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--lef", action="append", default=None, help="cell/macro LEF (repeatable)")
    ap.add_argument("--design", default="")
    a = ap.parse_args(argv)

    assert_pre_route(a.feature_def)
    gmeta_path = os.path.join(a.labels_dir, "congestion_grid.json")
    gmeta = json.load(open(gmeta_path))
    grid = Grid(gmeta)
    lefs = a.lef or lef.cell_lef_paths()
    if not lefs:
        raise SystemExit("no cell LEF: set SC_LEF (run via run_images.sh) or pass --lef")
    feats, fstats = build_features(a.feature_def, grid, lefs)
    labs, lstats = build_labels(a.labels_dir, grid)

    os.makedirs(os.path.join(a.out_dir, "png"), exist_ok=True)
    np.savez_compressed(os.path.join(a.out_dir, "features.npz"),
                        **{k: feats[k].astype(np.float32) for k in FEATURES})
    np.savez_compressed(os.path.join(a.out_dir, "labels.npz"),
                        **{k: labs[k].astype(np.float32) for k in LABELS})
    for k in FEATURES:
        write_png(os.path.join(a.out_dir, "png", "feature_%s.png" % k), feats[k])
    for k in LABELS:
        write_png(os.path.join(a.out_dir, "png", "label_%s.png" % k), labs[k])
    meta = {
        "design": a.design or gmeta.get("design"),
        "shape": [grid.nx, grid.ny],
        "index_order": "xy",
        "convention": "arrays are [x][y], shape (num_gcells_x, num_gcells_y), CircuitNet's order; "
                      "PNG previews are drawn y-up",
        "gcell_step_um": [grid.sx, grid.sy],
        "die_um": [grid.x0, grid.y0, grid.x1, grid.y1],
        "features": {k: {"unit": UNITS[k], "max": float(feats[k].max()), "sum": float(feats[k].sum())}
                     for k in FEATURES},
        "labels": {k: {"unit": UNITS[k], "max": float(labs[k].max()), "sum": float(labs[k].sum())}
                   for k in LABELS},
        "feature_stage": a.feature_stage,
        "sources": {"feature_def": {"path": os.path.abspath(a.feature_def), "sha256": sha256(a.feature_def)},
                    "grid": {"path": os.path.abspath(gmeta_path), "sha256": sha256(gmeta_path)},
                    "lefs": lefs},
        "feature_stats": fstats,
        "label_stats": lstats,
        "leakage_rule": "features read only the %s DEF (checked: no routed wires); labels are post-route"
                        % a.feature_stage,
    }
    json.dump(meta, open(os.path.join(a.out_dir, "image_meta.json"), "w"), indent=1)
    print("images: %dx%d  features=%s  labels=%s  -> %s" % (grid.nx, grid.ny, FEATURES, LABELS, a.out_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
