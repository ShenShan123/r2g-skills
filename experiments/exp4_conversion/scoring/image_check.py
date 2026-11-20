#!/usr/bin/env python3
"""Exp 4 (conversion) image scorer for spec_image/CONTRACT_IMAGE.md (fallback/reference).

Ground truth: raw DEF/LEF facts from the V1 module exp4_validation/gt_raw.py (own parsers),
CONTRACT_IMAGE quantities re-derived here with numpy (exact rectangle overlap per GCell);
the label grids come from defgraph_check.congestion_grids (the same contract text as the
graph congestion label). Comparison: exp4_validation/match.value_compare, per array.

    image_check.py truth <design_dir> <tech_dir> <truth.npz>
    image_check.py score <truth.npz> <out_dir> <score.json>
    image_check.py design <design_dir> <tech_dir> <truth.npz> <res_dir> LABEL=OUT_DIR ...
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import defgraph_check as DG  # noqa: E402  (also puts the V1 dir on sys.path)
G, M = DG.G, DG.M

FEATURES = ["cell_density", "macro_region", "rudy", "pin_rudy", "pin_density"]
LABELS = ["congestion", "congestion_raw"]
TOL = {"cell_density": (1e-5, 1e-5), "macro_region": (1e-5, 1e-5), "rudy": (1e-5, 1e-5),
       "pin_rudy": (1e-5, 1e-5), "pin_density": (0.0, 0.0), "congestion": (1e-6, 1e-5),
       "congestion_raw": (1e-6, 1e-5)}
DBU = 2000.0
# IMG_BIN=dbu: bin pin points with exact DBU arithmetic (sensitivity variant, see RESULTS.md)
import os  # noqa: E402
EXACT_BIN = os.environ.get("IMG_BIN", "") == "dbu"


def splat(arr, xe, ye, rect, v):
    """arr[x][y] += v * area(rect ∩ cell); xe/ye = clipped cell edges (um), length n+1."""
    xa, ya, xb, yb = rect
    xa, ya, xb, yb = max(xa, xe[0]), max(ya, ye[0]), min(xb, xe[-1]), min(yb, ye[-1])
    if xb <= xa or yb <= ya:
        return
    ox = np.clip(np.minimum(xb, xe[1:]) - np.maximum(xa, xe[:-1]), 0, None)
    oy = np.clip(np.minimum(yb, ye[1:]) - np.maximum(ya, ye[:-1]), 0, None)
    ix, iy = np.flatnonzero(ox), np.flatnonzero(oy)
    if ix.size and iy.size:
        arr[np.ix_(ix, iy)] += v * np.outer(ox[ix], oy[iy])


def truth(design_dir, tech_dir):
    d, T = Path(design_dir), Path(tech_dir)
    layers, macros = G.parse_lef([T / "lef" / DG.TECH_LEF, T / "lef" / DG.CELL_LEF])
    route = d / "5_route.def"
    dbu, die, _ = G.def_header(route)
    U, Gs, sx, sy, nx, ny = DG.congestion_grids(route, die, layers)
    X0, Y0, X1, Y1 = (v / DBU for v in die)
    xe = np.minimum(X0 + np.arange(nx + 1) * sx / DBU, X1); xe[-1] = X1
    ye = np.minimum(Y0 + np.arange(ny + 1) * sy / DBU, Y1); ye[-1] = Y1
    area = np.outer(np.diff(xe), np.diff(ye))
    place = d / "3_place.def"
    comps = G.parse_components(place)
    pins = G.parse_pins(place)
    nets, _, _ = G.parse_nets(place)
    std, mac = np.zeros((nx, ny)), np.zeros((nx, ny))
    for c in comps.values():
        m = macros.get(c["master"])
        if c["status"] not in ("PLACED", "FIXED", "COVER") or not m or m["w"] is None:
            continue
        w, h = (m["h"], m["w"]) if c["orient"] in ("E", "W", "FE", "FW") else (m["w"], m["h"])
        x, y = c["x"] / DBU, c["y"] / DBU
        splat(mac if m["cls"] == "BLOCK" else std, xe, ye, (x, y, x + w, y + h), 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        cd = np.where(area > 0, std / area, 0.0)
        mr = np.where(area > 0, mac / area, 0.0)

    def point(inst, pin):
        c = comps.get(inst)
        if c is None or c["status"] not in ("PLACED", "FIXED", "COVER"):
            return None
        x0, y0 = c["x"] / DBU, c["y"] / DBU
        m = macros.get(c["master"])
        rects = (m or {}).get("pins", {}).get(pin, {}).get("rects") if m else None
        if not rects:
            return x0, y0
        px = sum((r[0] + r[2]) / 2 for r in rects) / len(rects)
        py = sum((r[1] + r[3]) / 2 for r in rects) / len(rects)
        dx, dy = G.orient_point(px, py, m["w"], m["h"], c["orient"] or "N")
        return x0 + dx, y0 + dy

    rudy, prudy, pden = np.zeros((nx, ny)), np.zeros((nx, ny)), np.zeros((nx, ny))
    gx, gy = sx / DBU, sy / DBU
    for n, r in nets.items():
        if (r["use"] or "SIGNAL").upper() in ("POWER", "GROUND"):
            continue
        pts = []
        for io in r["ios"]:
            p = pins.get(io)
            if p and p["x"] is not None:
                pts.append((p["x"] / DBU, p["y"] / DBU))
        for i, p in r["conns"]:
            q = point(i, p)
            if q is not None:
                pts.append(q)
        if len(pts) < 2:
            continue
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        w, h = max(max(xs) - min(xs), gx), max(max(ys) - min(ys), gy)
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        dens = (w + h) / (w * h)
        splat(rudy, xe, ye, (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), dens)
        for x, y in pts:
            if not (X0 <= x <= X1 and Y0 <= y <= Y1):
                continue
            if EXACT_BIN:   # floor((x-X0)/sx) evaluated exactly in DBU (a point ON a GCell line goes up)
                i = min(math.floor((x * DBU - die[0]) / sx + 1e-9), nx - 1)
                j = min(math.floor((y * DBU - die[1]) / sy + 1e-9), ny - 1)
            else:           # the same expression in floating-point um (def-graph build_images / V1 checker)
                i = min(int((x - X0) // gx), nx - 1); j = min(int((y - Y0) // gy), ny - 1)
            prudy[i, j] += dens; pden[i, j] += 1
    with np.errstate(divide="ignore", invalid="ignore"):
        ru = np.where(area > 0, rudy / area, 0.0)
    return dict(cell_density=cd, macro_region=mr, rudy=ru, pin_rudy=prudy, pin_density=pden,
                congestion=Gs, congestion_raw=U)


def score(T, out_dir):
    groups = {}
    claim = {}
    for fn, names in (("features.npz", FEATURES), ("labels.npz", LABELS)):
        z = np.load(Path(out_dir) / fn, allow_pickle=False)
        for k in names:
            claim[k] = z[k] if k in z.files else None
    for k in FEATURES + LABELS:
        t = np.asarray(T[k], float)
        c = claim.get(k)
        if c is None or c.shape != t.shape:
            groups["IMG_" + k] = dict(n=int(t.size), matched=0, rate=0.0, passed=False,
                                      error="missing" if c is None else f"shape {c.shape} vs {t.shape}")
            continue
        r = M.value_compare(None, c.ravel(), t.ravel(), TOL[k])
        groups["IMG_" + k] = r
    s = M.summarize(groups)
    return dict(groups=groups, all_groups_pass=s["passed"], failed_groups=s["failed_groups"])


def main():
    cmd = sys.argv[1]
    if cmd == "truth":
        np.savez(sys.argv[4], **truth(sys.argv[2], sys.argv[3]))
    elif cmd == "score":
        T = dict(np.load(sys.argv[2]))
        res = score(T, sys.argv[3])
        Path(sys.argv[4]).write_text(json.dumps(res, indent=1, default=float))
        print(json.dumps({k: res[k] for k in ("all_groups_pass", "failed_groups")}))
    elif cmd == "design":
        dd, td, tp, rd = sys.argv[2:6]
        if not Path(tp).is_file():
            tmp = tp + ".tmp.npz"
            np.savez(tmp, **truth(dd, td))
            Path(tmp).replace(tp)
        T = dict(np.load(tp))
        Path(rd).mkdir(parents=True, exist_ok=True)
        for spec in sys.argv[6:]:
            lab, od = spec.split("=", 1)
            try:
                res = score(T, od)
            except Exception as exc:                  # noqa: BLE001
                res = dict(error=f"{type(exc).__name__}: {exc}", all_groups_pass=False)
            res["label"] = lab
            Path(rd, f"imgscore_{lab}.json").write_text(json.dumps(res, indent=1, default=float))
            print(lab, json.dumps({k: res.get(k) for k in ("all_groups_pass", "failed_groups", "error")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
