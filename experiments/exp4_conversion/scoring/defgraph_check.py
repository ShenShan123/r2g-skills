#!/usr/bin/env python3
"""Exp 4 (conversion) correctness scorer for the def-graph b/c contract (spec/CONTRACT.md).

Three layers, kept apart:
  * ground truth  -- the V1 study's format-independent module (exp4_validation/gt_raw.py:
    own DEF/LEF/Liberty/SPEF parsers) supplies every raw fact; `truth()` below derives the
    CONTRACT.md quantities from those facts (kept sets, ids, positions, congestion grid,
    path delay from an own OpenSTA call `timing_truth.tcl`). No converter code is imported.
  * adapter       -- `claims()` reads a converter's b_graph.pt / c_graph.pt into name-keyed
    claims (torch only to unpickle).
  * comparison    -- exp4_validation/match.py (set_compare / value_compare), with the
    tolerances frozen in PROTOCOL.md section 7 (TOL below).

    defgraph_check.py truth  <design_dir> <tech_dir> <truth.pkl> [--openroad BIN]
    defgraph_check.py score  <truth.pkl> <out_dir> <score.json>

tech_dir is the ORFS nangate45 platform dir (lib/*.lib, lef/*.lef) -- the layout gt_raw.build
expects. Names are compared after deleting backslashes (CONTRACT.md section 2).
"""
import argparse
import json
import math
import os
import pickle
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
V1 = Path(os.environ.get("EXP4_V1_DIR", HERE.parent.parent / "exp4_validation"))
sys.path.insert(0, str(V1))
import gt_raw as G  # noqa: E402
import match as M   # noqa: E402

GATE, NET, IOPIN, PIN = 0, 1, 2, 3
DBU = 2000.0
# Frozen tolerances (PROTOCOL.md section 7 == CONTRACT.md section 9): (atol, rtol)
TOL = {"coord": (1e-4, 1e-6), "hpwl": (6e-3, 1e-6), "lib": (1e-6, 1e-5), "cong": (1e-6, 1e-5),
       "timing": (1e-4, 1e-6), "wl_um": (2e-4, 1e-6), "wl_label": (1e-6, 1e-6), "gcap": (1e-5, 1e-5),
       "exact": (0.0, 0.0)}
ORIENT = {"N": 0, "S": 1, "E": 2, "W": 3, "FN": 4, "FS": 5, "FE": 6, "FW": 7}
STATUS = {"PLACED": 0, "FIXED": 1, "UNPLACED": 2}
PINDIR = {"INPUT": 0, "OUTPUT": 1, "INOUT": 2, "FEEDTHRU": 3}
SC_LIB = "NangateOpenCellLibrary_typical.lib"
TECH_LEF = "NangateOpenCellLibrary.tech.lef"
CELL_LEF = "NangateOpenCellLibrary.macro.mod.lef"


def canon(s):
    return s.replace("\\", "")


# ============================================================================ truth
def net_type(name, use):
    use = (use or "SIGNAL").upper()
    if use == "POWER":
        return 1
    if use == "GROUND":
        return 2
    n = name.lower()
    toks = [t for t in re.split(r"[^a-z0-9]+", n) if t]
    if use == "CLOCK" or any("clk" in t or t == "clock" for t in toks):
        return 3
    if "reset" in n or n.startswith("rst") or any(t.startswith("rst") or "reset" in t for t in toks):
        return 4
    if "scan" in n or any(t.startswith("scan") or t in {"se", "si", "so", "test", "testmode"} for t in toks):
        return 5
    return 0


def pin_type(pin, lpin):
    p = pin.upper()
    d = ((lpin or {}).get("dir") or "").lower()
    if p in {"VDD", "VPWR", "VCC", "VCCA", "VCCD"}:
        return 12
    if p in {"VSS", "VGND", "GND", "VSSA", "VSSD"}:
        return 13
    if d in ("inout", "feedthru"):
        return 11
    if (lpin or {}).get("clock") or "CLK" in p or p in {"CK", "CP", "CLOCK"}:
        return 5
    if p.startswith("RST") or "RESET" in p or p in {"RN", "RESETN", "RSTN"}:
        return 6
    if p.startswith("SET") or p in {"SN", "SETN"}:
        return 7
    if p in {"E", "EN", "OE", "TE", "GATE"}:
        return 8
    if p.startswith("SCAN") or p in {"SE", "SI", "SO"}:
        return 9
    if p in {"S", "S0", "S1", "SEL", "SELECT"} and d != "output":
        return 10
    if d == "input":
        return {"A": 0, "B": 1, "C": 2}.get(p[:1], 3)
    if d == "output":
        return 4
    return 14


def net_layers(def_path, layer_names):
    """{net: set(routing layers of its ROUTED/FIXED/COVER/NEW statements)} (own token scan)."""
    lut = {n.lower(): n.lower() for n in layer_names}
    out = {}
    for t in G._statements(def_path, "NETS"):
        layers = set()
        for k in range(2, len(t) - 1):
            if (t[k] in ("ROUTED", "FIXED", "COVER") and t[k - 1] == "+") or t[k] == "NEW":
                lay = t[k + 1].lower()
                if lay in lut:
                    layers.add(lay)
        out[canon(t[1])] = layers          # canonical key: gt_raw may key nets raw or canonical
    return out


def gcell_steps(def_path):
    sx = sy = None
    with open(def_path, errors="replace") as f:
        for line in f:
            if line.startswith("GCELLGRID"):
                p = line.split()
                if p[1] == "X":
                    sx = int(p[p.index("STEP") + 1])
                elif p[1] == "Y":
                    sy = int(p[p.index("STEP") + 1])
            elif line.startswith("COMPONENTS"):
                break
    return sx, sy


def gauss_reflect(u, sigma=1.0, radius=4):
    k = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    k /= k.sum()
    out = u.astype(np.float64)
    for ax in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[ax] = (radius, radius)
        p = np.pad(out, pad, mode="symmetric")
        acc = np.zeros_like(out)
        n = out.shape[ax]
        for j, w in enumerate(k):
            sl = [slice(None), slice(None)]
            sl[ax] = slice(j, j + n)
            acc += w * p[tuple(sl)]
        out = acc
    return out


def congestion_grids(def_path, die, lef_layers):
    """CONTRACT.md 6.1 / CONTRACT_IMAGE.md 4 -> (U, G, sx, sy, nx, ny), arrays [x][y]."""
    sx, sy = gcell_steps(def_path)
    _, segs, _ = G.parse_nets(def_path, with_routes=True)
    nx = max(1, math.ceil((die[2] - die[0]) / sx))
    ny = max(1, math.ceil((die[3] - die[1]) / sy))
    dh = np.zeros((nx, ny))
    dv = np.zeros((nx, ny))

    def add(dem, fixed_idx, a, b, step, fixed_n, run_n, horiz):
        lo, hi = min(a, b), max(a, b)
        if not (0 <= fixed_idx < fixed_n):
            return
        c = lo
        while c < hi:
            g = c // step
            nxt = min(hi, (g + 1) * step)
            if 0 <= g < run_n:
                if horiz:
                    dem[g, fixed_idx] += (nxt - c) / DBU
                else:
                    dem[fixed_idx, g] += (nxt - c) / DBU
            c = nxt

    for rows in segs.values():
        for _net, ax, ay, bx, by in rows:
            if ax != bx:
                add(dh, ay // sy, ax, bx, sx, ny, nx, True)
            if ay != by:
                add(dv, bx // sx, ay, by, sy, nx, ny, False)
    gw, gh = sx / DBU, sy / DBU
    ch = sum(gw * gh / L["pitch_um"] for L in lef_layers.values() if L["dir"] == "HORIZONTAL" and L["pitch_um"])
    cv = sum(gw * gh / L["pitch_um"] for L in lef_layers.values() if L["dir"] == "VERTICAL" and L["pitch_um"])
    u = np.maximum(dh / ch, dv / cv)
    return u, gauss_reflect(u), sx, sy, nx, ny


def congestion(def_path, die, lef_layers, macros, comps, gates):
    """CONTRACT.md 6.1 -> {gate: (raw mean G, mean sqrt G)}."""
    u, g, sx, sy, nx, ny = congestion_grids(def_path, die, lef_layers)
    out = {}
    for name in gates:
        c = comps[name]
        m = macros.get(c["master"])
        x, y = c["x"], c["y"]
        w, h = (m["w"], m["h"]) if m else (0.0, 0.0)
        if c["orient"] in ("E", "W", "FE", "FW"):
            w, h = h, w
        x1, y1 = x + w * DBU, y + h * DBU
        cx = [min(max(int(v) // sx, 0), nx - 1) for v in (x, x1)]
        cy = [min(max(int(v) // sy, 0), ny - 1) for v in (y, y1)]
        blk = g[cx[0]:cx[1] + 1, cy[0]:cy[1] + 1]
        out[name] = (float(blk.mean()), float(np.sqrt(np.maximum(blk, 0)).mean()))
    return out


def spef_port_ground(spef, port_names):
    """{canon net: grounded cap (fF) on the net's top-level port nodes} (own SPEF scan)."""
    ports = {canon(p) for p in port_names}
    nmap, out = {}, defaultdict(float)
    scale, cur, in_cap, sect = 1.0, None, False, None
    with open(spef, errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("*C_UNIT"):
                v, u = s.split()[1:3]
                scale = float(v) * {"PF": 1000.0, "FF": 1.0}[u.upper()]
            elif s.startswith("*NAME_MAP"):
                sect = "map"
            elif sect == "map" and s.startswith("*") and s[1:2].isdigit():
                k, v = s.split(None, 1)
                nmap[k] = v
            elif s.startswith("*D_NET"):
                sect = "net"
                nm = s.split()[1]
                cur = canon(nmap.get(nm, nm))
                in_cap = False
            elif s.startswith("*CAP"):
                in_cap = True
            elif s.startswith("*RES") or s.startswith("*CONN") or s.startswith("*END"):
                in_cap = False
            elif in_cap and cur is not None:
                p = s.split()
                if len(p) == 3:
                    node = canon(nmap.get(p[1], p[1]))
                    if node in ports:
                        out[cur] += float(p[2]) * scale
    return dict(out)


def sta_truth(design_dir, tech_dir, openroad):
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "t.tsv"
        env = dict(os.environ, TT_LEFS=f"{tech_dir}/lef/{TECH_LEF} {tech_dir}/lef/{CELL_LEF}",
                   TT_LIB=f"{tech_dir}/lib/{SC_LIB}", TT_DEF=f"{design_dir}/5_route.def",
                   TT_SDC=f"{design_dir}/constraint.sdc", TT_OUT=str(out))
        r = subprocess.run([openroad, "-no_splash", "-exit", str(HERE / "timing_truth.tcl")],
                           env=env, capture_output=True, text=True)
        if r.returncode or not out.is_file():
            raise RuntimeError("timing truth failed: " + (r.stdout + r.stderr)[-2000:])
        lines = out.read_text().splitlines()
    period = float(lines[0].split("\t")[1])
    slack = {}
    for ln in lines[1:]:
        n, s = ln.split("\t")
        slack[canon(n)] = None if s == "NA" else float(s)
    return period, slack


def truth(design_dir, tech_dir, openroad="openroad"):
    design_dir, tech_dir = Path(design_dir), Path(tech_dir)
    gt = G.build(design_dir, tech_dir)
    lib, lef = gt["lib"], gt["lef"]
    rt = gt["stages"]["5_route"]
    comps, iopins, nets = rt["comps"], rt["iopins"], rt["nets"]
    die = rt["die"]
    def_path = design_dir / "5_route.def"
    vocab = sorted(k.upper() for k, c in lib.items() if Path(c["lib"]).name == SC_LIB)
    vid = {k: i for i, k in enumerate(vocab)}
    # ---- kept sets (CONTRACT 3)
    ntype = {n: net_type(n, r["use"]) for n, r in nets.items()}
    io_type = {p: net_type(r["net"] or "", r["use"]) for p, r in iopins.items()}
    pins = sorted({(i, p) for n, r in nets.items() if ntype[n] == 0 for i, p in r["conns"]})
    pin_net = {}
    for n, r in nets.items():
        if ntype[n] == 0:
            for i, p in r["conns"]:
                pin_net[(i, p)] = n
    gates = sorted({i for i, _ in pins if i in comps and not re.search("FILL|TAP", comps[i]["master"], re.I)})
    gset = set(gates)
    pins = [k for k in pins if k[0] in gset]
    kept_io = sorted(p for p, t in io_type.items() if t == 0)
    netset = {pin_net[k] for k in pins} | {iopins[p]["net"] for p in kept_io if ntype.get(iopins[p]["net"]) == 0}
    knets = sorted(netset)
    io_edges = sorted((p, iopins[p]["net"]) for p in kept_io if iopins[p]["net"] in netset)
    # ---- gate features
    gfeat = {}
    for g in gates:
        c = comps[g]
        m = c["master"]
        L = lib.get(m) or {}
        gfeat[g] = dict(cell_type_id=vid.get(m.upper(), len(vocab)), cell_area=L.get("area") or 0.0,
                        cell_power=L.get("leakage") or 0.0, x_um=(c["x"] or 0) / DBU, y_um=(c["y"] or 0) / DBU,
                        orientation_id=ORIENT.get(c["orient"], -1), placement_status_id=STATUS.get(c["status"], -1))
    # ---- net features
    layers = net_layers(def_path, lef["layers"])
    macros = lef["macros"]

    def pin_point(inst, pin):
        c = comps[inst]
        x0, y0 = (c["x"] or 0) / DBU, (c["y"] or 0) / DBU
        m = macros.get(c["master"])
        rects = (m or {}).get("pins", {}).get(pin, {}).get("rects") if m else None
        if not rects:
            return x0, y0
        px = sum((r[0] + r[2]) / 2 for r in rects) / len(rects)
        py = sum((r[1] + r[3]) / 2 for r in rects) / len(rects)
        dx, dy = G.orient_point(px, py, m["w"], m["h"], c["orient"] or "N")
        return x0 + dx, y0 + dy

    nfeat = {}
    for n in knets:
        r = nets[n]
        drv = snk = 0
        pts = []
        for i, p in r["conns"]:
            d = ((lib.get(comps[i]["master"], {}).get("pins", {}).get(p) or {}).get("dir") or "") if i in comps else ""
            drv += d in ("output", "inout")
            snk += d in ("input", "inout")
            if i in comps:
                pts.append(pin_point(i, p))
        for io in r["ios"]:
            d = (iopins.get(io, {}).get("dir") or "").upper()
            drv += d in ("INPUT", "INOUT", "FEEDTHRU")
            snk += d in ("OUTPUT", "INOUT", "FEEDTHRU")
            if io in iopins and iopins[io]["x"] is not None:
                pts.append((iopins[io]["x"] / DBU, iopins[io]["y"] / DBU))
        pc = len(r["conns"]) + len(r["ios"])
        hp = (max(p[0] for p in pts) - min(p[0] for p in pts) + max(p[1] for p in pts) - min(p[1] for p in pts)) if pts else 0.0
        nfeat[n] = dict(net_type_id=ntype[n], fanout=max(0, pc - 1), pin_count=pc, num_drivers=drv, num_sinks=snk,
                        connects_macro_flag=0, num_layer=len(layers.get(canon(n), ())), hpwl_um=hp)
    # ---- iopin features
    taps = [((c["x"]) / DBU, (c["y"]) / DBU) for c in comps.values() if "TAP" in c["master"].upper() and c["x"] is not None]
    ifeat = {}
    for p in kept_io:
        r = iopins[p]
        x, y = (r["x"] or 0) / DBU, (r["y"] or 0) / DBU
        ifeat[p] = dict(pin_x_um=x, pin_y_um=y,
                        nearest_tap_distance_um=min(math.hypot(x - a, y - b) for a, b in taps) if taps else 0.0,
                        pin_direction_id=PINDIR.get((r["dir"] or "").upper(), -1))
    # ---- pin features
    port_g = spef_port_ground(design_dir / "6_final.spef", iopins.keys())
    netcap = {}
    for n in knets:
        s = 0.0
        for i, p in nets[n]["conns"]:
            if i in comps:
                s += (lib.get(comps[i]["master"], {}).get("pins", {}).get(p) or {}).get("cap_fF") or 0.0
        netcap[n] = s + port_g.get(canon(n), 0.0)
    pfeat = {}
    for i, p in pins:
        lp = lib.get(comps[i]["master"], {}).get("pins", {}).get(p)
        pfeat[(i, p)] = dict(pin_type_id=pin_type(p, lp), sum_pin_cap_fF=netcap[pin_net[(i, p)]])
    # ---- labels
    cong = congestion(def_path, die, lef["layers"], macros, comps, gates)
    period, slack = sta_truth(design_dir, tech_dir, openroad)
    pdelay = {}
    for g in gates:
        s = slack.get(canon(g))
        pdelay[g] = 0.0 if s is None else max(0.0, period - s)
    wl = {n: gt["route"]["net_len_um"].get(n, 0.0) for n in knets}
    spef = gt.get("spef") or {}
    gcap = {n: (spef[canon(n)]["ground"] if canon(n) in spef else math.nan) for n in knets}
    return dict(design=design_dir.name, gates=gates, nets=knets, iopins=kept_io, pins=pins, pin_net=pin_net,
                io_edges=io_edges, gfeat=gfeat, nfeat=nfeat, ifeat=ifeat, pfeat=pfeat, cong=cong, period=period,
                pdelay=pdelay, wl=wl, gcap=gcap, gt_seconds=gt["meta"]["seconds"],
                n_vocab=len(vocab), gt_raw_sha256=_sha(V1 / "gt_raw.py"), match_sha256=_sha(V1 / "match.py"))


def _sha(p):
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ============================================================================ adapter
def claims(out_dir):
    import torch
    res = {}
    for v in "bc":
        d = torch.load(Path(out_dir) / f"{v}_graph.pt", map_location="cpu", weights_only=False)
        c = {k: (getattr(d, k).detach().cpu().numpy().astype(np.float64) if k != "edge_index" else getattr(d, k).cpu().numpy())
             for k in ("x", "y", "y_raw", "edge_index") if k in d}
        c["names"] = [canon(s) for s in d.node_name]
        c["type"] = c["x"][:, 0].astype(int)
        if v == "c":
            for k in ("edge_attr", "edge_type", "edge_y", "edge_y_raw"):
                c[k] = getattr(d, k).detach().cpu().numpy().astype(np.float64 if k != "edge_type" else np.int64)
        res[v] = c
    return res


# ============================================================================ comparison
GATE_COLS = ["cell_type_id", "cell_area", "cell_power", "x_um", "y_um", "orientation_id", "placement_status_id"]
NET_COLS = ["net_type_id", "fanout", "pin_count", "num_drivers", "num_sinks", "connects_macro_flag", "num_layer", "hpwl_um"]
IO_COLS = ["pin_x_um", "pin_y_um", "nearest_tap_distance_um", "pin_direction_id"]
PIN_COLS = ["pin_type_id", "sum_pin_cap_fF"]


def _merge(results):
    """Combine several value_compare results into one group result."""
    n = sum(r["n"] for r in results)
    m = sum(r["matched"] for r in results)
    ex = [e for r in results for e in r.get("examples", [])][:8]
    return dict(n=n, matched=m, rate=(m / n if n else 1.0), passed=all(r["passed"] for r in results),
                max_abs=max([r.get("max_abs", 0.0) for r in results] or [0.0]),
                max_rel=max([r.get("max_rel", 0.0) for r in results] or [0.0]),
                parts={r["_name"]: {k: r[k] for k in ("n", "matched", "passed") if k in r} for r in results},
                examples=ex)


def _vc(name, names, claim, truth_, tol):
    r = M.value_compare(names, claim, truth_, TOL[tol] if isinstance(tol, str) else tol)
    r["_name"] = name
    for e in r.get("examples", []):
        e["field"] = name
    return r


def score(T, C):
    groups = {}
    tn = {GATE: [canon(g) for g in T["gates"]], NET: [canon(n) for n in T["nets"]],
          IOPIN: [canon(p) for p in T["iopins"]], PIN: [canon(f"{i}/{p}") for i, p in T["pins"]]}
    b, c = C["b"], C["c"]
    # identity
    for typ, key in ((GATE, "ID_gates"), (NET, "ID_nets"), (IOPIN, "ID_iopins"), (PIN, "ID_pins")):
        claim = [n for n, t in zip(b["names"], b["type"]) if t == typ]
        groups[key] = M.set_compare(claim, tn[typ])
    ctruth = [(t, n) for t in (GATE, NET, IOPIN) for n in tn[t]]
    groups["ID_c_nodes"] = M.set_compare(list(zip(c["type"].tolist(), c["names"])), ctruth)
    # order
    exp_b = [n for t in (GATE, NET, IOPIN, PIN) for n in tn[t]]
    exp_c = [n for t in (GATE, NET, IOPIN) for n in tn[t]]
    ok_b = sum(a == e for a, e in zip(b["names"], exp_b))
    ok_c = sum(a == e for a, e in zip(c["names"], exp_c))
    nn = len(exp_b) + len(exp_c)
    groups["ORDER"] = dict(n=nn, matched=ok_b + ok_c, rate=(ok_b + ok_c) / nn if nn else 1.0,
                           passed=(b["names"] == exp_b and c["names"] == exp_c))
    # edges
    tg = lambda d, i: (int(d["type"][i]), d["names"][i])  # noqa: E731
    eb = b["edge_index"]
    claim_b = [(tg(b, s), tg(b, t)) for s, t in zip(eb[0].tolist(), eb[1].tolist())]
    truth_b = []
    for (i, p) in T["pins"]:
        g, pn, n = (GATE, canon(i)), (PIN, canon(f"{i}/{p}")), (NET, canon(T["pin_net"][(i, p)]))
        truth_b += [(g, pn), (pn, g), (pn, n), (n, pn)]
    for io, n in T["io_edges"]:
        truth_b += [((IOPIN, canon(io)), (NET, canon(n))), ((NET, canon(n)), (IOPIN, canon(io)))]
    groups["EDGES_b"] = M.set_compare(claim_b, truth_b)
    ec = c["edge_index"]
    claim_c = [(tg(c, s), tg(c, t), int(e)) for s, t, e in zip(ec[0].tolist(), ec[1].tolist(), c["edge_type"].tolist())]
    truth_c = []
    for (i, p) in T["pins"]:
        g, n = (GATE, canon(i)), (NET, canon(T["pin_net"][(i, p)]))
        truth_c += [(g, n, 0), (n, g, 0)]
    for io, n in T["io_edges"]:
        truth_c += [((IOPIN, canon(io)), (NET, canon(n)), 1), ((NET, canon(n)), (IOPIN, canon(io)), 1)]
    groups["EDGES_c"] = M.set_compare(claim_c, truth_c)

    # node values: align claims by name; a missing node gives NaN (counts as a mismatch)
    def col(d, typ, names_t, j, src="x"):
        idx = {n: k for k, (n, t) in enumerate(zip(d["names"], d["type"])) if t == typ}
        arr = d[src]
        return np.array([arr[idx[n], j] if n in idx else np.nan for n in names_t])

    def feat_group(typ, cols, tols, fdict, keys, views=("b", "c")):
        res = []
        for v in views:
            for j, (cn, tol) in enumerate(zip(cols, tols)):
                truth_v = np.array([fdict[k][cn] for k in keys], dtype=np.float64)
                res.append(_vc(f"{v}:{cn}", tn[typ], col(C[v], typ, tn[typ], 2 + j), truth_v, tol))
        return _merge(res)

    gk = T["gates"]
    groups["F_gate_categorical"] = feat_group(GATE, ["cell_type_id"], ["exact"], T["gfeat"], gk)
    groups["F_gate_categorical"] = _merge([
        _vc(f"{v}:{cn}", tn[GATE], col(C[v], GATE, tn[GATE], 2 + GATE_COLS.index(cn)),
            np.array([T["gfeat"][k][cn] for k in gk], float), "exact")
        for v in "bc" for cn in ("cell_type_id", "orientation_id", "placement_status_id")])
    groups["F_gate_liberty"] = _merge([
        _vc(f"{v}:{cn}", tn[GATE], col(C[v], GATE, tn[GATE], 2 + GATE_COLS.index(cn)),
            np.array([T["gfeat"][k][cn] for k in gk], float), "lib")
        for v in "bc" for cn in ("cell_area", "cell_power")])
    groups["F_gate_position"] = _merge([
        _vc(f"{v}:{cn}", tn[GATE], col(C[v], GATE, tn[GATE], 2 + GATE_COLS.index(cn)),
            np.array([T["gfeat"][k][cn] for k in gk], float), "coord")
        for v in "bc" for cn in ("x_um", "y_um")])
    nk = T["nets"]
    groups["F_net_integer"] = _merge([
        _vc(f"{v}:{cn}", tn[NET], col(C[v], NET, tn[NET], 2 + NET_COLS.index(cn)),
            np.array([T["nfeat"][k][cn] for k in nk], float), "exact")
        for v in "bc" for cn in NET_COLS[:-1]])
    groups["F_net_hpwl"] = _merge([
        _vc(f"{v}:hpwl_um", tn[NET], col(C[v], NET, tn[NET], 9), np.array([T["nfeat"][k]["hpwl_um"] for k in nk], float), "hpwl")
        for v in "bc"])
    ik = T["iopins"]
    groups["F_iopin"] = _merge([
        _vc(f"{v}:{cn}", tn[IOPIN], col(C[v], IOPIN, tn[IOPIN], 2 + IO_COLS.index(cn)),
            np.array([T["ifeat"][k][cn] for k in ik], float), "exact" if cn == "pin_direction_id" else "coord")
        for v in "bc" for cn in IO_COLS])
    pk = T["pins"]
    groups["F_pin"] = _merge([
        _vc(f"b:{cn}", tn[PIN], col(b, PIN, tn[PIN], 2 + j), np.array([T["pfeat"][k][cn] for k in pk], float),
            "exact" if cn == "pin_type_id" else "lib") for j, cn in enumerate(PIN_COLS)])
    # c pin edges: per (gate, net), the multiset of [pin_type_id, sum_pin_cap, timing, timing_raw]
    tgrp = defaultdict(list)
    for (i, p) in pk:
        f = T["pfeat"][(i, p)]
        pd = T["pdelay"][i]
        tgrp[(canon(i), canon(T["pin_net"][(i, p)]))].append((f["pin_type_id"], f["sum_pin_cap_fF"], math.log1p(pd), pd))
    cgrp = defaultdict(list)
    for k in range(0, ec.shape[1], 1):
        s, t = int(ec[0, k]), int(ec[1, k])
        if c["edge_type"][k] == 0 and c["type"][s] == GATE and c["type"][t] == NET:
            cgrp[(c["names"][s], c["names"][t])].append((c["edge_attr"][k, 0], c["edge_attr"][k, 1], c["edge_y"][k, 3], c["edge_y_raw"][k, 3]))
    rows_t, rows_c, rnames = [], [], []
    for key, tl in tgrp.items():
        tl = sorted(tl)
        cl = sorted(cgrp.get(key, []), key=lambda r: (r[0], r[1]))
        for j, tr in enumerate(tl):
            rows_t.append(tr)
            rows_c.append(cl[j] if j < len(cl) else (np.nan,) * 4)
            rnames.append(key)
    rt_, rc_ = np.array(rows_t, float).reshape(-1, 4), np.array(rows_c, float).reshape(-1, 4)
    groups["F_c_pin_edges"] = _merge([_vc("c:edge_attr pin_type_id", rnames, rc_[:, 0], rt_[:, 0], "exact"),
                                      _vc("c:edge_attr sum_pin_cap_fF", rnames, rc_[:, 1], rt_[:, 1], "lib")])
    # labels
    cong_raw = np.array([T["cong"][g][0] for g in gk]); cong_lab = np.array([T["cong"][g][1] for g in gk])
    groups["L_congestion"] = _merge([r for v in "bc" for r in (
        _vc(f"{v}:y1", tn[GATE], col(C[v], GATE, tn[GATE], 1, "y"), cong_lab, "cong"),
        _vc(f"{v}:y_raw1", tn[GATE], col(C[v], GATE, tn[GATE], 1, "y_raw"), cong_raw, "cong"))])
    pdel = np.array([T["pdelay"][i] for i, _ in pk])
    groups["L_timing"] = _merge([
        _vc("b:y3", tn[PIN], col(b, PIN, tn[PIN], 3, "y"), np.log1p(pdel), "timing"),
        _vc("b:y_raw3", tn[PIN], col(b, PIN, tn[PIN], 3, "y_raw"), pdel, "timing"),
        _vc("c:edge_y3", rnames, rc_[:, 2], rt_[:, 2], "timing"),
        _vc("c:edge_y_raw3", rnames, rc_[:, 3], rt_[:, 3], "timing")])
    wl = np.array([T["wl"][n] for n in nk])
    groups["L_wirelength"] = _merge([r for v in "bc" for r in (
        _vc(f"{v}:y4", tn[NET], col(C[v], NET, tn[NET], 4, "y"), np.log1p(wl), "wl_label"),
        _vc(f"{v}:y_raw4", tn[NET], col(C[v], NET, tn[NET], 4, "y_raw"), wl, "wl_um"))])
    gc = np.array([T["gcap"][n] for n in nk])
    groups["L_ground_cap"] = _merge([r for v in "bc" for r in (
        _vc(f"{v}:y5", tn[NET], col(C[v], NET, tn[NET], 5, "y"), np.log1p(gc), "gcap"),
        _vc(f"{v}:y_raw5", tn[NET], col(C[v], NET, tn[NET], 5, "y_raw"), gc, "gcap"))])
    for k, g in groups.items():
        g.setdefault("rate", g.get("f1", 0.0))
    summ = M.summarize(groups)
    return dict(design=T["design"], groups=groups, all_groups_pass=summ["passed"], failed_groups=summ["failed_groups"])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("truth"); t.add_argument("design_dir"); t.add_argument("tech_dir"); t.add_argument("out")
    t.add_argument("--openroad", default=os.environ.get("OPENROAD_EXE", "openroad"))
    s = sub.add_parser("score"); s.add_argument("truth"); s.add_argument("out_dir"); s.add_argument("out")
    d = sub.add_parser("design", help="truth (cached) + score every LABEL=OUT_DIR into RES_DIR/score_LABEL.json")
    d.add_argument("design_dir"); d.add_argument("tech_dir"); d.add_argument("truth"); d.add_argument("res_dir")
    d.add_argument("arms", nargs="*")
    d.add_argument("--openroad", default=os.environ.get("OPENROAD_EXE", "openroad"))
    a = ap.parse_args()
    if a.cmd == "design":
        tp = Path(a.truth)
        if not tp.is_file():
            T = truth(a.design_dir, a.tech_dir, a.openroad)
            tmp = tp.with_suffix(".tmp")
            with open(tmp, "wb") as f:
                pickle.dump(T, f, protocol=4)
            tmp.replace(tp)
        with open(tp, "rb") as f:
            T = pickle.load(f)
        Path(a.res_dir).mkdir(parents=True, exist_ok=True)
        for spec in a.arms:
            label, od = spec.split("=", 1)
            try:
                res = score(T, claims(od))
            except Exception as exc:                  # noqa: BLE001
                res = dict(design=T["design"], error=f"{type(exc).__name__}: {exc}", all_groups_pass=False)
            res["label"] = label
            Path(a.res_dir, f"score_{label}.json").write_text(json.dumps(res, indent=1, default=float))
            print(label, json.dumps({k: res.get(k) for k in ("all_groups_pass", "failed_groups", "error")}))
        return 0
    if a.cmd == "truth":
        T = truth(a.design_dir, a.tech_dir, a.openroad)
        with open(a.out, "wb") as f:
            pickle.dump(T, f, protocol=4)
        print("TRUTH_OK", T["design"], len(T["gates"]), len(T["nets"]), len(T["pins"]))
        return 0
    with open(a.truth, "rb") as f:
        T = pickle.load(f)
    try:
        C = claims(a.out_dir)
        res = score(T, C)
    except Exception as exc:                          # noqa: BLE001
        res = dict(design=T["design"], error=f"{type(exc).__name__}: {exc}", all_groups_pass=False)
    Path(a.out).write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps({k: res.get(k) for k in ("design", "all_groups_pass", "failed_groups", "error")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
