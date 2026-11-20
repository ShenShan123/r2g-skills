#!/usr/bin/env python3
"""Build one design's multimodal sample (placement-stage images + netlist graph, post-route congestion label).

  mm_prep.py RUN_DIR OUT_DIR DESIGN --platform-dir P --code CODE_DIR [--place-def DEF]

Inputs are a SIGNED-OFF backend run (DRC+LVS clean): its 6_final.def (labels) and its placement-stage DEF
(features; exported beforehand from 3_place.odb). Everything is computed by def-graph's own code, unchanged:
  labels/   extract_congestion.py on 6_final.def -> congestion_grid.{csv,json} (the grid) + cell_congestion.csv
  images/   build_images.py --feature-stage place on the placement DEF (5 channels) + verify_images.py (17 checks)
  graph.npz NEW: the placement-stage netlist on the SAME gcell grid. Nodes are cells (std cells, macros) and top
            IO pins; hyperedges are signal nets (POWER/GROUND/clock-tree nets excluded as in def-graph). Per cell:
            master id, w, h, centre, pin count, gcell footprint; per net: degree, placement bbox and its gcell
            footprint; per pin: (cell, net) incidence with the pin's in-cell position (LEF geometry, oriented).
Leakage rule: graph and images read ONLY the placement DEF; routing appears only in the labels.
"""
import argparse
import json
import os
import subprocess
import sys

import numpy as np

SKIP_USE = {"POWER", "GROUND"}


def _lef_pin_dirs(path):
    """{MACRO_UPPER: {PIN: 'OUTPUT'|'INPUT'|'INOUT'}} from a cell LEF (DIRECTION statements)."""
    out, mac, pin = {}, None, None
    for ln in open(path, errors="replace"):
        t = ln.split()
        if not t:
            continue
        if t[0] == "MACRO" and len(t) > 1:
            mac = t[1].upper(); out[mac] = {}
        elif t[0] == "PIN" and mac and len(t) > 1:
            pin = t[1]
        elif t[0] == "DIRECTION" and mac and pin and len(t) > 1:
            out[mac][pin] = t[1].rstrip(";").upper()
        elif t[0] == "END" and len(t) > 1 and t[1] == pin:
            pin = None
    return out


def _def_port_dirs(path):
    """{port: DIRECTION} from the DEF PINS section."""
    out, on, buf = {}, False, ""
    for ln in open(path, errors="replace"):
        st = ln.strip()
        if not on:
            on = st.startswith("PINS ")
            continue
        if st.startswith("END PINS"):
            break
        buf += " " + st
        if st.endswith(";"):
            t = buf.split()
            if len(t) >= 2 and t[0] == "-" and "DIRECTION" in t:
                out[t[1]] = t[t.index("DIRECTION") + 1].upper()
            buf = ""
    return out


def _labels_and_images(a, ext, env, final_def, sclef):
    r = subprocess.run([sys.executable, os.path.join(ext, "labels", "extract_congestion.py"), final_def,
                        os.path.join(a.out, "labels", "cell_congestion.csv"), a.design], env=env,
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(os.path.join(a.out, "labels", "congestion_grid.json")):
        print("LABEL_FAIL", r.stderr[-800:]); return 2
    import build_images                                                   # noqa: E402
    os.environ.update(SC_LEF=sclef, ADDITIONAL_LEFS="")
    build_images.main(["--feature-def", a.place_def, "--feature-stage", "place",
                       "--labels-dir", os.path.join(a.out, "labels"), "--out-dir", os.path.join(a.out, "images"),
                       "--lef", sclef, "--design", a.design])
    v = subprocess.run([sys.executable, os.path.join(ext, "images", "verify_images.py"), os.path.join(a.out, "images"),
                        "--labels-dir", os.path.join(a.out, "labels"), "--final-def", final_def, "--lef", sclef,
                        "--json", os.path.join(a.out, "images", "verify.json")], capture_output=True, text=True)
    if v.returncode != 0:
        print("VERIFY_FAIL", v.stdout[-800:]); return 3

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("out")
    ap.add_argument("design")
    ap.add_argument("--platform-dir", required=True)
    ap.add_argument("--platform", default="sky130hd")
    ap.add_argument("--code", required=True, help="dir holding def-graph's extract/ tree")
    ap.add_argument("--place-def", required=True)
    ap.add_argument("--tech-lef", default="sky130_fd_sc_hd.tlef", help="file name under <platform-dir>/lef")
    ap.add_argument("--sc-lef", default="sky130_fd_sc_hd_merged.lef", help="file name under <platform-dir>/lef")
    ap.add_argument("--graph-only", action="store_true", help="labels/images exist; rebuild graph.npz only")
    a = ap.parse_args()
    ext = os.path.join(a.code, "extract")
    sys.path.insert(0, ext)
    sys.path.insert(0, os.path.join(ext, "images"))
    from techlib import def_parse, lef                                   # noqa: E402
    tlef = os.path.join(a.platform_dir, "lef", a.tech_lef)
    sclef = os.path.join(a.platform_dir, "lef", a.sc_lef)
    os.makedirs(os.path.join(a.out, "labels"), exist_ok=True)
    env = dict(os.environ, R2G_PLATFORM=a.platform, TECH_LEF=tlef, SC_LEF=sclef, ADDITIONAL_LEFS="", R2G_EMIT_GRID="1")
    final_def = os.path.join(a.run, "6_final.def")
    if not a.graph_only:
        rc = _labels_and_images(a, ext, env, final_def, sclef)
        if rc:
            return rc
    # ---------------- graph on the same grid ----------------
    g = json.load(open(os.path.join(a.out, "labels", "congestion_grid.json")))
    nx, ny = g["gcell_size"]
    dbu_g = float(g["db_units"])
    x0, y0 = g["die_dbu"][0] / dbu_g, g["die_dbu"][1] / dbu_g
    x1, y1 = g["die_dbu"][2] / dbu_g, g["die_dbu"][3] / dbu_g
    sx, sy = g["grid_step_dbu"][0] / dbu_g, g["grid_step_dbu"][1] / dbu_g

    def gx(v):
        return int(min(max((v - x0) // sx, 0), nx - 1))

    def gy(v):
        return int(min(max((v - y0) // sy, 0), ny - 1))

    dbu = def_parse.parse_units(a.place_def) or 1000.0
    comps = def_parse.parse_components(a.place_def)
    nets = def_parse.parse_nets(a.place_def)
    io = def_parse.parse_io_pins(a.place_def)
    geom = lef.macro_pin_geometry([sclef])
    masters = sorted(geom)                                      # per-platform vocabulary (LEF order-independent)
    mid = {m: i for i, m in enumerate(masters)}
    names, feats, foot, kind = [], [], [], []
    idx = {}
    for inst, c in comps.items():
        if c.get("x") is None or c.get("status") not in ("PLACED", "FIXED", "COVER"):
            continue
        key = c["master"].upper()
        mac = geom.get(key)
        w, h = (mac["width"], mac["height"]) if mac else (0.0, 0.0)
        if c.get("orient") in ("E", "W", "FE", "FW"):
            w, h = h, w
        cx, cy = c["x"] / dbu + w / 2, c["y"] / dbu + h / 2
        idx[inst] = len(names)
        names.append(inst)
        kind.append(0)
        feats.append([mid.get(key, len(masters)), w, h, (cx - x0) / max(x1 - x0, 1e-9),
                      (cy - y0) / max(y1 - y0, 1e-9), 0.0])
        foot.append([gx(c["x"] / dbu), gy(c["y"] / dbu), gx(c["x"] / dbu + w), gy(c["y"] / dbu + h)])
    for pin, p in io.items():
        px, py = p["x"] / dbu, p["y"] / dbu
        idx[("PIN", pin)] = len(names)
        names.append("PIN:" + pin)
        kind.append(1)
        feats.append([len(masters) + 1, 0.0, 0.0, (px - x0) / max(x1 - x0, 1e-9), (py - y0) / max(y1 - y0, 1e-9), 0.0])
        foot.append([gx(px), gy(py), gx(px), gy(py)])
    ldir, pdir = _lef_pin_dirs(sclef), _def_port_dirs(a.place_def)
    pin_cell, pin_net, pin_xy, pin_drv = [], [], [], []
    net_feat, net_foot = [], []
    for name, n in nets.items():
        if n.get("use", "") in SKIP_USE:
            continue
        pts = []
        for inst, pin in n["conns"]:
            if inst == "PIN":
                k = ("PIN", pin)
                if k not in idx:
                    continue
                p = io[pin]
                pts.append((idx[k], p["x"] / dbu, p["y"] / dbu, int(pdir.get(pin) == "INPUT")))
            elif inst in idx:
                c = comps[inst]
                px, py = lef.pin_abs_pos_um(geom, c["x"] / dbu, c["y"] / dbu, c.get("orient"), c["master"], pin)
                pts.append((idx[inst], px, py, int(ldir.get(c["master"].upper(), {}).get(pin) == "OUTPUT")))
        if len(pts) < 2:
            continue
        j = len(net_feat)
        xs, ys = [p[1] for p in pts], [p[2] for p in pts]
        for ci, px, py, drv in pts:
            pin_drv.append(drv)
            pin_cell.append(ci)
            pin_net.append(j)
            pin_xy.append([(px - x0) / max(x1 - x0, 1e-9), (py - y0) / max(y1 - y0, 1e-9)])
            feats[ci][5] += 1
        net_feat.append([len(pts), max(xs) - min(xs), max(ys) - min(ys)])
        net_foot.append([gx(min(xs)), gy(min(ys)), gx(max(xs)), gy(max(ys))])
    np.savez_compressed(os.path.join(a.out, "graph.npz"),
                        cell_feat=np.asarray(feats, np.float32), cell_kind=np.asarray(kind, np.int8),
                        cell_foot=np.asarray(foot, np.int32), net_feat=np.asarray(net_feat, np.float32),
                        net_foot=np.asarray(net_foot, np.int32), pin_cell=np.asarray(pin_cell, np.int32),
                        pin_net=np.asarray(pin_net, np.int32), pin_xy=np.asarray(pin_xy, np.float32), pin_drv=np.asarray(pin_drv, np.int8),
                        grid=np.asarray([nx, ny], np.int32), n_masters=np.asarray([len(masters)], np.int32))
    json.dump({"design": a.design, "cells": int(sum(1 for k in kind if k == 0)), "io_pins": int(sum(kind)),
               "nets": len(net_feat), "pins": len(pin_cell), "grid": [nx, ny],
               "verify": json.load(open(os.path.join(a.out, "images", "verify.json")))["status"]},
              open(os.path.join(a.out, "sample.json"), "w"), indent=1)
    print("OK", a.design, nx, ny, len(names), len(net_feat))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
