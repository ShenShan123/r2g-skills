#!/usr/bin/env python3
"""Public FORMAT checker for the Exp 4 v2 task (def-graph's full graph product + images).

    python3 check_format_v2.py <out_dir> <design_dir> [--json report.json]

Checks the FORM of the output only, against def-graph's documented layout (SKILL.md,
graph-dataset.md, four-stage-dataset.md, B_VIEW_DATASET_STRUCTURE.md) and the v1 image contract:
files load, required keys / node and edge stores exist, dtypes and shapes, node-type blocks,
NaN placement of label slots, raw/normalized twins, fwd/rev interleaving, homo<->hetero
consistency, four-stage y <-> y_valid_mask, identical node sets across the four stages, image
arrays on the design's GCell grid. Power (IR drop) slots are excluded and never checked. It never
computes a correct value. Results are reported per task: graphs, four_stage (only when
<design_dir> holds 1_2_yosys.v), images. Exit 0 iff every applicable task passes.
"""
import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import torch

GATE, NET, IOPIN, PIN = 0, 1, 2, 3
TYPES = {"b": [0, 1, 2, 3], "c": [0, 1, 2], "d": [0, 2, 3], "e": [2, 3], "f": [0, 2]}
TNAME = {0: "gate", 1: "net", 2: "iopin", 3: "pin"}
STAGES = ("floorplan", "placement", "cts", "route")
FS_X = {"gate": 30, "net": 28, "io_pin": 30, "pin": 37}
FEATURES = ["cell_density", "macro_region", "rudy", "pin_rudy", "pin_density"]
LABELS = ["congestion", "congestion_raw"]


class R:
    def __init__(self):
        self.c = []

    def add(self, task, name, ok, detail=""):
        self.c.append({"task": task, "check": name, "ok": bool(ok), "detail": str(detail)[:300]})
        return bool(ok)


def nan_eq(a, b):
    return bool(((a == b) | (torch.isnan(a) & torch.isnan(b))).all())


def y_rules(v):
    """{slot: node types where the slot may be non-NaN}; slot 2 (IR drop) is excluded."""
    return {1: {GATE}, 3: {PIN}, 4: {NET}, 5: ({NET} if v in "bc" else {PIN} if v in "de" else set())}


def check_homo(r, v, g):
    t = f"graphs:{v}_homo"
    if not r.add(t, "is Data", type(g).__name__ == "Data", type(g).__name__):
        return None
    for k in ("x", "y", "y_raw", "edge_index", "node_name", "rc_edge_index", "rc_edge_type", "rc_edge_y", "rc_edge_y_raw"):
        r.add(t, f"has {k}", k in g)
    if v != "b":
        for k in ("edge_attr", "edge_type", "edge_y", "edge_y_raw"):
            r.add(t, f"has {k}", k in g)
    if not all(k in g for k in ("x", "y", "y_raw", "edge_index", "node_name")):
        return None
    x, y, yr, ei = g.x, g.y, g.y_raw, g.edge_index
    n = x.shape[0]
    r.add(t, "x float32 [N,10]", x.dtype == torch.float32 and tuple(x.shape) == (n, 10), tuple(x.shape))
    r.add(t, "y/y_raw float32 [N,6]", all(a.dtype == torch.float32 and tuple(a.shape) == (n, 6) for a in (y, yr)))
    r.add(t, "node_name len N", isinstance(g.node_name, (list, tuple)) and len(g.node_name) == n)
    nt = x[:, 0].long()
    r.add(t, "node types", sorted(set(nt.tolist())) == TYPES[v], sorted(set(nt.tolist())))
    blk = [TYPES[v].index(int(a)) if int(a) in TYPES[v] else 99 for a in nt.tolist()]
    r.add(t, "type blocks ordered", all(a <= b for a, b in zip(blk, blk[1:])))
    r.add(t, "y[:,0]==y_raw[:,0]==type", bool((y[:, 0] == x[:, 0]).all() and (yr[:, 0] == x[:, 0]).all()))
    r.add(t, "y/y_raw same NaN placement", bool((torch.isnan(y) == torch.isnan(yr)).all()))
    for s, ok_types in y_rules(v).items():
        bad = [TNAME[k] for k in TYPES[v] if k not in ok_types and not torch.isnan(y[nt == k, s]).all()]
        r.add(t, f"y[:,{s}] NaN outside its node type", not bad, bad)
    r.add(t, "edge_index int64 [2,E], in range", ei.dtype == torch.int64 and ei.dim() == 2 and ei.shape[0] == 2
          and (ei.numel() == 0 or (int(ei.min()) >= 0 and int(ei.max()) < n)))
    e = ei.shape[1]
    r.add(t, "edges interleaved fwd/rev", e % 2 == 0 and bool((ei[:, 1::2] == ei.flip(0)[:, 0::2]).all()))
    if v != "b" and "edge_attr" in g and "edge_y" in g:
        r.add(t, "edge_attr float32 [E,8]", g.edge_attr.dtype == torch.float32 and tuple(g.edge_attr.shape) == (e, 8))
        r.add(t, "edge_type int64 [E]", g.edge_type.dtype == torch.int64 and tuple(g.edge_type.shape) == (e,))
        r.add(t, "edge_y/edge_y_raw float32 [E,6]", all(a.dtype == torch.float32 and tuple(a.shape) == (e, 6) for a in (g.edge_y, g.edge_y_raw)))
        if tuple(g.edge_y.shape) == (e, 6):
            r.add(t, "edge_y[:,0]==edge_type", bool((g.edge_y[:, 0] == g.edge_type.float()).all()))
            r.add(t, "edge_y[:,5] all NaN", bool(torch.isnan(g.edge_y[:, 5]).all()))
            r.add(t, "edge_y/edge_y_raw same NaN placement", bool((torch.isnan(g.edge_y) == torch.isnan(g.edge_y_raw)).all()))
            r.add(t, "fwd/rev rows identical", bool(torch.equal(g.edge_attr[0::2], g.edge_attr[1::2])) and nan_eq(g.edge_y[0::2], g.edge_y[1::2]))
    if all(k in g for k in ("rc_edge_index", "rc_edge_type", "rc_edge_y", "rc_edge_y_raw")):
        q = g.rc_edge_index.shape[1]
        r.add(t, "rc_edge_index int64 [2,R] in range, interleaved", g.rc_edge_index.dtype == torch.int64
              and q % 2 == 0 and (q == 0 or (int(g.rc_edge_index.min()) >= 0 and int(g.rc_edge_index.max()) < n))
              and bool((g.rc_edge_index[:, 1::2] == g.rc_edge_index.flip(0)[:, 0::2]).all()))
        r.add(t, "rc_edge_y/raw float32 [R,3]", all(a.dtype == torch.float32 and tuple(a.shape) == (q, 3) for a in (g.rc_edge_y, g.rc_edge_y_raw)))
        if q and tuple(g.rc_edge_y.shape) == (q, 3):
            ty = g.rc_edge_type
            r.add(t, "rc_edge_y[:,0]==rc_edge_type", bool((g.rc_edge_y[:, 0] == ty.float()).all()))
            r.add(t, "rc off-type column NaN", bool(torch.isnan(g.rc_edge_y[ty == 0, 2]).all() and torch.isnan(g.rc_edge_y[ty == 1, 1]).all()))
            r.add(t, "rc label/raw same NaN placement", bool((torch.isnan(g.rc_edge_y) == torch.isnan(g.rc_edge_y_raw)).all()))
            if v == "f":
                r.add(t, "f has no RC edges", q == 0)
    return nt


def check_hetero(r, v, h, homo_types):
    t = f"graphs:{v}_hetero"
    if not r.add(t, "is HeteroData", type(h).__name__ == "HeteroData", type(h).__name__):
        return
    want = {TNAME[k] for k in TYPES[v]}
    r.add(t, "node types", set(h.node_types) == want, sorted(h.node_types))
    for nt_ in h.node_types:
        s = h[nt_]
        n = s.x.shape[0] if "x" in s else -1
        r.add(t, f"{nt_}.x float32 [n,9]", "x" in s and s.x.dtype == torch.float32 and s.x.shape[1] == 9)
        r.add(t, f"{nt_}.y/y_raw [n,5]", all(k in s and tuple(s[k].shape) == (n, 5) for k in ("y", "y_raw")))
        r.add(t, f"{nt_}.node_name len n", "node_name" in s and len(s.node_name) == n)
        if homo_types is not None:
            k = {vv: kk for kk, vv in TNAME.items()}.get(nt_)
            r.add(t, f"{nt_} count == homo", k is not None and int((homo_types == k).sum()) == n)
    for et in h.edge_types:
        s = h[et]
        ok = "edge_index" in s and s.edge_index.dtype == torch.int64 and s.edge_index.shape[0] == 2
        r.add(t, f"{'|'.join(et)} edge_index int64 [2,E]", ok)
        if not ok:
            continue
        e = s.edge_index.shape[1]
        if et[1].startswith("rc_"):
            r.add(t, f"{'|'.join(et)} rc_edge_y [E,2]", "rc_edge_y" in s and tuple(s.rc_edge_y.shape) == (e, 2))
        elif v != "b":
            r.add(t, f"{'|'.join(et)} edge_attr [E,8], edge_y [E,5]", "edge_attr" in s and tuple(s.edge_attr.shape) == (e, 8)
                  and "edge_y" in s and tuple(s.edge_y.shape) == (e, 5))
        r.add(t, f"{'|'.join(et)} endpoints in range", e == 0 or (int(s.edge_index[0].max()) < h[et[0]].x.shape[0]
                                                                  and int(s.edge_index[1].max()) < h[et[2]].x.shape[0]))


def check_four_stage(r, root):
    t = "four_stage"
    sets = {}
    for st in STAGES:
        p = root / "generated" / "stages" / st / "heterograph.pt"
        if not r.add(t, f"{st}/heterograph.pt exists", p.is_file(), str(p)):
            continue
        try:
            h = torch.load(p, map_location="cpu", weights_only=False)
        except Exception as exc:                       # noqa: BLE001
            r.add(t, f"{st} loads", False, exc); continue
        if not r.add(t, f"{st} is HeteroData", type(h).__name__ == "HeteroData"):
            continue
        r.add(t, f"{st} node types", set(h.node_types) == set(FS_X), sorted(h.node_types))
        counts = {}
        for nt_, w in FS_X.items():
            if nt_ not in h.node_types:
                continue
            s = h[nt_]
            n = s.x.shape[0]
            counts[nt_] = n
            r.add(t, f"{st} {nt_}.x float32 [n,{w}]", s.x.dtype == torch.float32 and tuple(s.x.shape) == (n, w), tuple(s.x.shape))
            if r.add(t, f"{st} {nt_}.y [n,2] + bool y_valid_mask", "y" in s and "y_valid_mask" in s and tuple(s.y.shape) == (n, 2)
                     and s.y_valid_mask.dtype == torch.bool and tuple(s.y_valid_mask.shape) == (n, 2)):
                cols = [0] if nt_ == "gate" else [0, 1]          # gate col 1 = ir_drop_mV: power, excluded
                r.add(t, f"{st} {nt_} isfinite(y)==y_valid_mask", all(bool((torch.isfinite(s.y[:, c]) == s.y_valid_mask[:, c]).all()) for c in cols))
        sets[st] = counts
        logical = [("gate", "has", "pin"), ("pin", "connects_to", "net"), ("io_pin", "connects_to", "net")]
        for et in logical:
            ok = et in h.edge_types and "edge_attr" in h[et] and h[et].edge_attr.dim() == 2 and h[et].edge_attr.shape[1] == 2
            r.add(t, f"{st} {'|'.join(et)} present, edge_attr [E,2]", ok)
        cg = ("gate", "congestion_geom", "gate")
        if st in ("floorplan", "placement"):
            r.add(t, f"{st} congestion_geom absent", cg not in h.edge_types)
        else:
            r.add(t, f"{st} congestion_geom present, edge_attr [E,1]", cg in h.edge_types and h[cg].edge_attr.shape[1] == 1)
        for et in h.edge_types:
            if et[1] in ("timing_path", "rc_coupling", "rc_resistance"):
                s = h[et]
                r.add(t, f"{st} {'|'.join(et)} supervision-only (edge_attr [E,0]) with edge_y + edge_y_mask",
                      "edge_attr" in s and s.edge_attr.dim() == 2 and s.edge_attr.shape[1] == 0 and "edge_y" in s and "edge_y_mask" in s)
        r.add(t, f"{st} global_features [19]", "global_features" in h and int(h.global_features.numel()) == 19)
    if len(sets) == 4:
        r.add(t, "identical node counts in all four stages", len({json.dumps(c, sort_keys=True) for c in sets.values()}) == 1, sets)


def grid_shape(def_path):
    sx = sy = die = None
    with open(def_path, errors="replace") as f:
        for line in f:
            if line.startswith("DIEAREA"):
                v = [int(x) for x in re.findall(r"-?\d+", line)]
                die = (min(v[0::2]), min(v[1::2]), max(v[0::2]), max(v[1::2]))
            elif line.startswith("GCELLGRID"):
                p = line.split()
                if p[1] == "X":
                    sx = int(p[p.index("STEP") + 1])
                else:
                    sy = int(p[p.index("STEP") + 1])
            elif line.startswith("COMPONENTS"):
                break
    return max(1, math.ceil((die[2] - die[0]) / sx)), max(1, math.ceil((die[3] - die[1]) / sy))


def check_images(r, root, design):
    t = "images"
    arrays = {}
    for fn, names in (("features.npz", FEATURES), ("labels.npz", LABELS)):
        p = root / fn
        if not r.add(t, f"{fn} exists", p.is_file()):
            continue
        try:
            z = np.load(p, allow_pickle=False)
        except Exception as exc:                       # noqa: BLE001
            r.add(t, f"{fn} loads", False, exc); continue
        extra = set(z.files) - set(names) - ({"ir_drop"} if fn == "labels.npz" else set())
        r.add(t, f"{fn} has {names} (ir_drop optional, ignored)", set(names) <= set(z.files) and not extra, sorted(z.files))
        for k in names:
            if k in z.files:
                arrays[k] = z[k]
    exp = grid_shape(design / "6_final.def")
    for k, a in arrays.items():
        r.add(t, f"{k} float32 2-D shape {exp}", a.dtype == np.float32 and a.shape == exp, (a.dtype, a.shape))
        r.add(t, f"{k} finite, >= 0", bool(np.isfinite(a).all() and (a >= 0).all()))
    if "pin_density" in arrays:
        r.add(t, "pin_density integer-valued", bool(np.all(arrays["pin_density"] == np.round(arrays["pin_density"]))))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("design_dir", type=Path)
    ap.add_argument("--json", type=Path)
    a = ap.parse_args()
    r = R()
    ds = a.out_dir / "dataset"
    for v in "bcdef":
        types = None
        for fn, fun in ((f"{v}_graph_homo.pt", "homo"), (f"{v}_graph.pt", "hetero")):
            p = ds / fn
            if not r.add(f"graphs:{v}_{fun}", f"{fn} exists", p.is_file()):
                continue
            try:
                g = torch.load(p, map_location="cpu", weights_only=False)
            except Exception as exc:                   # noqa: BLE001
                r.add(f"graphs:{v}_{fun}", "loads", False, exc); continue
            if fun == "homo":
                types = check_homo(r, v, g)
            else:
                check_hetero(r, v, g, types)
    four = (a.design_dir / "1_2_yosys.v").is_file()
    if four:
        check_four_stage(r, a.out_dir / "four_stage")
    check_images(r, a.out_dir / "images", a.design_dir)
    tasks = {}
    for c in r.c:
        k = c["task"].split(":")[0]
        tasks.setdefault(k, []).append(c)
    summary = {k: {"ok": all(c["ok"] for c in v), "n_checks": len(v), "failed": [c for c in v if not c["ok"]][:20]}
               for k, v in tasks.items()}
    out = {"ok": all(s["ok"] for s in summary.values()), "tasks": summary}
    if a.json:
        a.json.write_text(json.dumps({**out, "checks": r.c}, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
